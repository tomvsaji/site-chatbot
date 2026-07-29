from __future__ import annotations

import json
import http.client
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CASES_PATH = Path(
    os.getenv("EVAL_CASES_PATH", BASE_DIR / "tests" / "eval_cases.json")
)
API_URL = os.getenv("EVAL_API_URL", "http://localhost:8080/api/chat")
MODEL_LABEL = os.getenv("EVAL_MODEL_LABEL", "current")
OUTPUT_PATH = os.getenv("EVAL_OUTPUT_PATH", "")
WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)


@dataclass
class TurnResult:
    scenario_id: str
    turn_id: str
    question: str
    answer: str
    sources: list[dict]
    duration_seconds: float
    score: float
    passed: bool
    failures: list[str]
    concepts_found: list[str]


def request_answer(
    question: str, history: list[dict], request_number: int
) -> tuple[int, dict, float]:
    payload = json.dumps({"question": question, "history": history}).encode()
    # Give each benchmark request a documentation-only IP so application rate
    # limits do not distort a local, sequential model comparison.
    octet_3 = (request_number // 250) % 250
    octet_4 = request_number % 250 + 1
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Forwarded-For": f"198.51.{octet_3}.{octet_4}",
        },
        method="POST",
    )
    started = time.monotonic()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                return (
                    response.status,
                    json.load(response),
                    time.monotonic() - started,
                )
        except urllib.error.HTTPError as error:
            try:
                body = json.load(error)
            except json.JSONDecodeError:
                body = {"error": error.read().decode(errors="replace")}
            return error.code, body, time.monotonic() - started
        except (
            urllib.error.URLError,
            http.client.RemoteDisconnected,
            ConnectionResetError,
            TimeoutError,
        ) as error:
            last_error = error
            if attempt < 2:
                time.sleep(12)
    return (
        503,
        {"error": f"model service unavailable after retries: {last_error}"},
        time.monotonic() - started,
    )


def normalized(text: str) -> str:
    return " ".join(WORD_RE.findall(text.lower()))


def phrase_present(answer: str, alternatives: list[str]) -> bool:
    lowered = answer.lower()
    return any(alternative.lower() in lowered for alternative in alternatives)


def evaluate_turn(
    scenario_id: str,
    turn: dict,
    status: int,
    result: dict,
    duration: float,
    fallback: str,
    previous_answer: str,
) -> TurnResult:
    expect = turn["expect"]
    answer = str(result.get("answer", ""))
    sources = result.get("sources", [])
    failures: list[str] = []
    concepts_found: list[str] = []

    if status != 200:
        failures.append(f"HTTP {status}: {result}")
    elif expect.get("abstain"):
        if answer != fallback or sources:
            failures.append("expected the exact source-free abstention")
    else:
        if answer == fallback:
            failures.append("unexpected abstention")

        returned_names = {
            str(source.get("name", "")) for source in sources if isinstance(source, dict)
        }
        for expected_source in expect.get("sources", []):
            if expected_source not in returned_names:
                failures.append(f"missing source {expected_source!r}")

        citations = {int(number) for number in re.findall(r"\[(\d+)]", answer)}
        returned_numbers = {
            source.get("number") for source in sources if isinstance(source, dict)
        }
        if not citations or citations != returned_numbers:
            failures.append("answer citations do not match returned sources")

        for concept in expect.get("concepts", []):
            if phrase_present(answer, concept["any_of"]):
                concepts_found.append(concept["label"])
        minimum = expect.get("min_concepts", len(expect.get("concepts", [])))
        if len(concepts_found) < minimum:
            failures.append(
                f"concept coverage {len(concepts_found)}/{len(expect.get('concepts', []))}; "
                f"minimum is {minimum}"
            )

        for forbidden in expect.get("forbidden", []):
            if forbidden.lower() in answer.lower():
                failures.append(f"contains forbidden phrase {forbidden!r}")

        word_count = len(WORD_RE.findall(re.sub(r"\[\d+]", "", answer)))
        if word_count < expect.get("min_words", 0):
            failures.append(
                f"too concise: {word_count} words; minimum is {expect['min_words']}"
            )
        if word_count > expect.get("max_words", sys.maxsize):
            failures.append(
                f"too long: {word_count} words; maximum is {expect['max_words']}"
            )

        if previous_answer and "max_previous_similarity" in expect:
            similarity = SequenceMatcher(
                None, normalized(previous_answer), normalized(answer)
            ).ratio()
            if similarity > expect["max_previous_similarity"]:
                failures.append(
                    f"repeats prior answer: similarity {similarity:.2f}; "
                    f"maximum is {expect['max_previous_similarity']:.2f}"
                )

        if "Source:" in answer or "<reference_passages>" in answer:
            failures.append("raw retrieval context leaked into answer")

    concepts = expect.get("concepts", [])
    coverage = len(concepts_found) / len(concepts) if concepts else 1.0
    passed = not failures
    # Coverage gives partial credit, while passing all behavioral gates is
    # deliberately required for a perfect score.
    score = coverage if passed else min(coverage, 0.99)
    if expect.get("abstain"):
        score = 1.0 if passed else 0.0

    return TurnResult(
        scenario_id=scenario_id,
        turn_id=turn["id"],
        question=turn["question"],
        answer=answer,
        sources=sources,
        duration_seconds=duration,
        score=score,
        passed=passed,
        failures=failures,
        concepts_found=concepts_found,
    )


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def main() -> int:
    suite = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    scenarios = suite["scenarios"]
    selected_tags = {
        value.strip()
        for value in os.getenv("EVAL_TAGS", "").split(",")
        if value.strip()
    }
    selected_scenarios = {
        value.strip()
        for value in os.getenv("EVAL_SCENARIOS", "").split(",")
        if value.strip()
    }
    if selected_tags:
        scenarios = [
            scenario
            for scenario in scenarios
            if selected_tags.intersection(scenario.get("tags", []))
        ]
    if selected_scenarios:
        scenarios = [
            scenario for scenario in scenarios if scenario["id"] in selected_scenarios
        ]

    results: list[TurnResult] = []
    request_number = 0
    for scenario in scenarios:
        history: list[dict] = []
        previous_answer = ""
        print(f"\n[{scenario['id']}]")
        for turn in scenario["turns"]:
            request_number += 1
            status, response, duration = request_answer(
                turn["question"], history, request_number
            )
            evaluated = evaluate_turn(
                scenario["id"],
                turn,
                status,
                response,
                duration,
                suite["fallback"],
                previous_answer,
            )
            results.append(evaluated)
            outcome = "PASS" if evaluated.passed else "FAIL"
            print(
                f"{outcome:4} {turn['id']:<28} "
                f"{duration:6.1f}s score={evaluated.score:.2f}"
            )
            print(f"     Q: {turn['question']}")
            print(f"     A: {evaluated.answer}")
            for failure in evaluated.failures:
                print(f"     - {failure}")

            # The model sees its own preceding answer, reproducing the website's
            # real conversation dynamics instead of supplying an ideal history.
            history.extend(
                [
                    {"role": "user", "content": turn["question"]},
                    {"role": "assistant", "content": evaluated.answer},
                ]
            )
            history = history[-8:]
            previous_answer = evaluated.answer

    durations = [result.duration_seconds for result in results]
    passed = sum(result.passed for result in results)
    mean_score = statistics.fmean(result.score for result in results) if results else 0
    summary = {
        "model": MODEL_LABEL,
        "suite_version": suite["version"],
        "cases_path": str(CASES_PATH),
        "turns": len(results),
        "passed": passed,
        "pass_rate": passed / len(results) if results else 0,
        "mean_score": mean_score,
        "latency_seconds": {
            "median": statistics.median(durations) if durations else 0,
            "p95": percentile(durations, 0.95),
            "max": max(durations, default=0),
        },
        "results": [result.__dict__ for result in results],
    }
    print(
        f"\n{MODEL_LABEL}: {passed}/{len(results)} passed "
        f"({summary['pass_rate']:.0%}); score={mean_score:.2f}; "
        f"median={summary['latency_seconds']['median']:.1f}s; "
        f"p95={summary['latency_seconds']['p95']:.1f}s"
    )
    if OUTPUT_PATH:
        output = Path(OUTPUT_PATH)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Report written to {output}")
    return 1 if passed != len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
