# SPDX-License-Identifier: MIT

"""Deterministic conformance evals for data-App Opportunities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_autosuggest import apps

CASES = [
    json.loads(line)
    for line in (Path(__file__).parents[1] / "evals" / "data-apps.jsonl")
    .read_text()
    .splitlines()
    if line
]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_data_app_eval(case):
    answer = apps.enforce(case["answer"])
    problems: list[str] = []
    apps.add_defects(answer, case["evidence"], problems)

    delivery = answer["suggested"][0]["delivery"]
    assert delivery["agent"] == "agent-application"
    assert delivery["turn_kind"] == "app"
    assert delivery["status"] == "proposed"
    assert delivery["confirmation_required"] is True
    assert delivery["proposed_actions"] == case["expected_actions"]
    assert problems == []
