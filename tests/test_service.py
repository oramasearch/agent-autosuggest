"""Service mode without a model: the turn contract, the health rule, and the trace."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent_autosuggest import service
from agent_autosuggest.retrieval import Corpus

PROFILE = {"industry": "saas", "persona": "head of customer success",
           "tables": [{"database": "datasets", "name": "accounts",
                       "columns": [{"name": "churned", "type": "bool"}]}]}


class ScriptedClient:
    def __init__(self, content: str) -> None:
        reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: reply))


def settings(tmp_path: Path) -> service.Settings:
    value = service.Settings()
    value.trace_dir = tmp_path / "traces"
    value.corpus_path = tmp_path / "missing.jsonl"
    return value


def test_a_turn_without_a_profile_is_refused(tmp_path):
    answer = service.run_turn({"request": "what could I build?"}, settings(tmp_path),
                              Corpus(tmp_path / "missing.jsonl"))
    assert not answer["ok"] and "profile" in answer["error"]


def test_a_turn_suggests_from_the_profile_and_records_a_trace(tmp_path):
    reply = json.dumps({"suggested": [{"intent": "predict churn", "solution": "S-1",
                                       "methodology": "gbdt"}], "declined": []})
    answer = service.run_turn({"context": {"profile": PROFILE}}, settings(tmp_path),
                              Corpus(tmp_path / "missing.jsonl"), ScriptedClient(reply))
    assert answer["ok"], answer
    assert answer["result"]["project_profile"]["suggested"][0]["intent"] == "predict churn"
    trace = json.loads((tmp_path / "traces" / f"{answer['trace_id']}.json").read_text())
    assert trace["profile"]["industry"] == "saas"


def test_health_fails_without_the_corpus(tmp_path):
    status, body = service.health(settings(tmp_path), Corpus(tmp_path / "missing.jsonl"))
    assert status == 503 and not body["ok"]
