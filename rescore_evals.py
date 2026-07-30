from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from evaluate import evaluate_turn, percentile


BASE_DIR = Path(__file__).resolve().parent
SUITE_PATH = BASE_DIR / "tests" / "eval_cases.json"


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python rescore_evals.py REPORT.json [...]")
        return 2

    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    turns = {
        (scenario["id"], turn["id"]): turn
        for scenario in suite["scenarios"]
        for turn in scenario["turns"]
    }

    for name in sys.argv[1:]:
        path = Path(name)
        report = json.loads(path.read_text(encoding="utf-8"))
        rescored = []
        previous_answers: dict[str, str] = {}
        for raw in report["results"]:
            key = (raw["scenario_id"], raw["turn_id"])
            turn = turns[key]
            result = evaluate_turn(
                raw["scenario_id"],
                turn,
                200,
                {"answer": raw["answer"], "sources": raw["sources"]},
                raw["duration_seconds"],
                suite["fallback"],
                previous_answers.get(raw["scenario_id"], ""),
            )
            previous_answers[raw["scenario_id"]] = raw["answer"]
            rescored.append(result)

        durations = [result.duration_seconds for result in rescored]
        passed = sum(result.passed for result in rescored)
        output = {
            **{key: value for key, value in report.items() if key != "results"},
            "suite_version": suite["version"],
            "scoring_note": "Replayed from preserved answers against the calibrated rubric.",
            "turns": len(rescored),
            "passed": passed,
            "pass_rate": passed / len(rescored) if rescored else 0,
            "mean_score": (
                statistics.fmean(result.score for result in rescored)
                if rescored
                else 0
            ),
            "latency_seconds": {
                "median": statistics.median(durations) if durations else 0,
                "p95": percentile(durations, 0.95),
                "max": max(durations, default=0),
            },
            "results": [result.__dict__ for result in rescored],
        }
        destination = path.with_name(f"{path.stem}-rescored.json")
        destination.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(
            f"{report['model']}: {passed}/{len(rescored)} "
            f"({output['pass_rate']:.0%}) -> {destination}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
