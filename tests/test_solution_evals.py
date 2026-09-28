"""Deterministic conformance evals for runtime-solution Opportunities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_autosuggest import solutions, validate

CASES = [
    json.loads(line)
    for line in (Path(__file__).parents[1] / "evals" / "runtime-solutions.jsonl")
    .read_text()
    .splitlines()
    if line
]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_runtime_solution_eval(case):
    solution = case["solution"]

    def query(capability, inputs):
        if capability == "list_solutions":
            return {"solutions": [solution]}
        if capability == "get_solution":
            assert inputs == {"solution": solution["id"]}
            return {"solution": solution}
        raise AssertionError(f"unexpected capability {capability}")

    solution_evidence = solutions.inspect(query)
    methodology_evidence = {"feature_plans": case["feature_plans"]}
    answer = solutions.enforce(
        case["answer"],
        solution_evidence,
        methodology_evidence,
        query,
    )

    bucket = answer[case["expected_bucket"]]
    assert len(bucket) == 1
    if case["expected_answer"] is not None:
        assert bucket[0]["answer"] == case["expected_answer"]
    assert validate.defects(answer, methodology_evidence, solution_evidence) == []


def test_a_numerical_success_claim_is_a_defect():
    solution = CASES[0]["solution"]
    solution_evidence = {
        "source": "list_solutions",
        "solutions": [solution],
        "details": {},
    }
    answer = CASES[0]["answer"] | {
        "summary": "This has a success chance of 80%",
    }

    problems = validate.defects(answer, {"feature_plans": []}, solution_evidence)

    assert "answer contains a numerical return or success claim" in problems
