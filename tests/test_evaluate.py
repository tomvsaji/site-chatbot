import json
import unittest
from pathlib import Path

from evaluate import evaluate_turn


FALLBACK = "I couldn't find that in Tom's published site data."
BASE_DIR = Path(__file__).resolve().parents[1]


class EvaluationLogicTests(unittest.TestCase):
    def test_eval_suite_has_unique_grounded_turns(self):
        suite = json.loads(
            (BASE_DIR / "tests" / "eval_cases.json").read_text(encoding="utf-8")
        )
        scenarios = suite["scenarios"]
        turns = [
            (scenario, turn)
            for scenario in scenarios
            for turn in scenario["turns"]
        ]
        self.assertEqual(len(turns), 25)
        self.assertEqual(
            sum(
                len(scenario["turns"])
                for scenario in scenarios
                if "core" in scenario.get("tags", [])
            ),
            23,
        )
        scenario_ids = [scenario["id"] for scenario in scenarios]
        self.assertEqual(len(scenario_ids), len(set(scenario_ids)))
        turn_ids = [
            f"{scenario['id']}/{turn['id']}" for scenario, turn in turns
        ]
        self.assertEqual(len(turn_ids), len(set(turn_ids)))

        for _, turn in turns:
            expect = turn["expect"]
            if expect.get("abstain"):
                self.assertEqual(expect, {"abstain": True})
                continue
            self.assertTrue(expect["sources"])
            self.assertTrue(expect["concepts"])
            self.assertLessEqual(
                expect["min_concepts"], len(expect["concepts"])
            )
            for source in expect["sources"]:
                self.assertTrue(
                    (BASE_DIR / "data" / source).is_file(),
                    f"missing source file for {turn['id']}: {source}",
                )

    def test_supported_turn_checks_source_concepts_length_and_citations(self):
        turn = {
            "id": "example",
            "question": "Why?",
            "expect": {
                "sources": ["article.md"],
                "concepts": [
                    {"label": "specialization", "any_of": ["narrow domain"]},
                    {"label": "training", "any_of": ["real IDE"]},
                ],
                "min_concepts": 2,
                "min_words": 8,
                "max_words": 30,
            },
        }
        result = evaluate_turn(
            "scenario",
            turn,
            200,
            {
                "answer": (
                    "Its narrow domain concentrates training, while a real IDE "
                    "makes the learning environment representative. [1]"
                ),
                "sources": [{"number": 1, "name": "article.md"}],
            },
            1.0,
            FALLBACK,
            "",
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 1.0)

    def test_abstention_must_have_no_sources(self):
        turn = {
            "id": "unknown",
            "question": "Unknown?",
            "expect": {"abstain": True},
        }
        result = evaluate_turn(
            "scenario",
            turn,
            200,
            {"answer": FALLBACK, "sources": [{"number": 1, "name": "facts.md"}]},
            1.0,
            FALLBACK,
            "",
        )
        self.assertFalse(result.passed)

    def test_repeated_answer_fails_similarity_gate(self):
        answer = (
            "Specialization makes the model efficient by focusing on a narrow "
            "coding workflow instead of broad general intelligence. [1]"
        )
        turn = {
            "id": "follow-up",
            "question": "But why?",
            "expect": {
                "sources": ["article.md"],
                "concepts": [
                    {"label": "focus", "any_of": ["narrow coding workflow"]}
                ],
                "min_concepts": 1,
                "min_words": 5,
                "max_previous_similarity": 0.7,
            },
        }
        result = evaluate_turn(
            "scenario",
            turn,
            200,
            {"answer": answer, "sources": [{"number": 1, "name": "article.md"}]},
            1.0,
            FALLBACK,
            answer,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("repeats prior answer" in item for item in result.failures))


if __name__ == "__main__":
    unittest.main()
