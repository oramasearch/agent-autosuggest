"""Service mode without a model: the turn contract, the health rule, and the trace."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_autosuggest import catalog, service, workspace
from agent_autosuggest.retrieval import Corpus

PROFILE = {"industry": "saas", "persona": "head of customer success",
           "tables": [{"database": "datasets", "name": "accounts",
                       "columns": [{"name": "churned", "type": "bool"}]}]}
CUSTOMERS_WITHOUT_LABEL = {
    "industry": "retail",
    "persona": "growth lead",
    "tables": [
        {
            "database": "datasets",
            "name": "customers",
            "columns": [
                {"name": "customer_id", "type": "string"},
                {"name": "country", "type": "string"},
                {"name": "lifetime_spend", "type": "number"},
            ],
        }
    ],
}
EVENT_LOGS = {
    "industry": "saas",
    "persona": "product lead",
    "tables": [
        {
            "database": "datasets",
            "name": "events",
            "columns": [
                {"name": "account_id", "type": "string"},
                {"name": "occurred_at", "type": "timestamp"},
                {"name": "event_name", "type": "string"},
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def built_catalogue(tmp_path, monkeypatch):
    built = tmp_path / "catalog.json"
    built.write_text(json.dumps({"solutions": [
        {"id": "S-1", "name": "Churn"},
        {"id": "S-3", "name": "Customer segmentation"},
        {"id": "S-7", "name": "Next best action"},
    ]}))
    monkeypatch.setenv("ZETTA_CATALOG", str(built))
    catalog.load.cache_clear()
    yield
    catalog.load.cache_clear()


class ScriptedClient:
    def __init__(self, content: str) -> None:
        reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: reply))


class FakeEngine:
    """A lake/Zetta read client with a current registry and per-dataset evidence."""

    def __init__(self, methodologies, analyses=None, plans=None):
        self.methodologies = methodologies
        self.analyses = analyses or {}
        self.plans = plans or {}
        self.calls: list[tuple[str, dict]] = []

    def query(self, capability, inputs):
        self.calls.append((capability, inputs))
        if capability == "list_methodologies":
            return {"methodologies": self.methodologies}
        if capability == "analyze_dataset":
            return self.analyses.get(inputs["dataset"], {})
        if capability == "plan_features":
            return self.plans.get((inputs["dataset"], inputs["methodology"]), {"ready": True})
        raise AssertionError(f"unexpected capability {capability}")


SEGMENTATION = {
    "id": "M-3",
    "name": "Segmentation / clustering",
    "solutions": ["S-3"],
    "runnable": True,
    "partial": False,
}
NEXT_BEST_ACTION = {
    "id": "M-7",
    "name": "Next best action",
    "solutions": ["S-7"],
    "runnable": False,
    "partial": False,
    "issue": "zetta#1042",
}


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
                                       "methodology": "M-1", "runnable": True,
                                       "why_this_data_supports_it": "accounts.churned is a label"}],
                        "declined": []})
    answer = service.run_turn({"context": {"profile": PROFILE}}, settings(tmp_path),
                              Corpus(tmp_path / "missing.jsonl"), ScriptedClient(reply))
    assert answer["ok"], answer
    assert answer["result"]["project_profile"]["suggested"][0]["intent"] == "predict churn"
    trace = json.loads((tmp_path / "traces" / f"{answer['trace_id']}.json").read_text())
    assert trace["profile"]["industry"] == "saas"
    assert trace["methodology_evidence"]["source"] == "old-engine fallback"


def test_health_fails_without_the_corpus(tmp_path):
    status, body = service.health(settings(tmp_path), Corpus(tmp_path / "missing.jsonl"))
    assert status == 503 and not body["ok"]


class FakeWorkspace:
    """lake's workspace path: list_datasets and get_dataset, recording what was asked."""

    def __init__(self, datasets):
        self.datasets = datasets
        self.urls: list[tuple[str, str]] = []

    def get(self, url: str, token: str):
        self.urls.append((url, token))
        if "/v1/list_datasets" in url:
            return {"ok": True, "value": {"datasets": [{"database": "datasets", "name": n}
                                                      for n in self.datasets]}}
        if "/v1/get_dataset" in url and "dataset=broken" not in url:
            return {"ok": True, "value": {"columns": [{"name": "churned"}], "rows": 200}}
        return {"ok": False, "error": {"message": "unreadable"}}


def test_scan_my_workspace_reads_the_profile_with_the_turns_credential(tmp_path):
    reply = json.dumps({"suggested": [{"intent": "predict churn", "solution": "S-1",
                                       "methodology": "M-1", "runnable": True,
                                       "why_this_data_supports_it": "accounts.churned is a label"}],
                        "declined": []})
    node = FakeWorkspace(["accounts", "broken"])
    answer = service.run_turn({"request": "?", "zetta_base_url": "http://lake/workspaces/c/zetta",
                               "zetta_token": "lat.t.secret"},
                              settings(tmp_path), Corpus(tmp_path / "missing.jsonl"),
                              ScriptedClient(reply), get=node.get)
    assert answer["ok"], answer
    assert all(url.startswith("http://lake/workspaces/c/zetta/v1/") for url, _ in node.urls)
    assert {token for _, token in node.urls} == {"lat.t.secret"}
    trace = json.loads((tmp_path / "traces" / f"{answer['trace_id']}.json").read_text())
    tables = trace["profile"]["tables"]
    assert [t["name"] for t in tables] == ["accounts", "broken"]
    assert tables[0]["rows"] == 200 and tables[1]["columns"] == []
    assert "lat.t.secret" not in json.dumps(trace), "the credential never reaches a trace"


def test_an_empty_workspace_is_told_to_load_data(tmp_path):
    answer = service.run_turn({"zetta_base_url": "http://lake/w", "zetta_token": "t"},
                              settings(tmp_path), Corpus(tmp_path / "missing.jsonl"),
                              get=FakeWorkspace([]).get)
    assert not answer["ok"] and "load data" in answer["error"]


def test_customer_table_without_a_label_suggests_runnable_segmentation(tmp_path):
    engine = FakeEngine(
        [SEGMENTATION, NEXT_BEST_ACTION],
        analyses={"customers": {"grain": "snapshot", "keys": ["customer_id"]}},
        plans={("customers", "M-3"): {"ready": True, "features": ["country", "lifetime_spend"]}},
    )
    reply = json.dumps({
        "suggested": [{
            "intent": "segment customers",
            "solution": "S-3",
            "methodology": "M-3",
            "runnable": True,
            "why_this_data_supports_it": "customers has country and lifetime_spend per customer_id",
        }],
        "declined": [],
    })
    answer = service.run_turn(
        {"profile": CUSTOMERS_WITHOUT_LABEL},
        settings(tmp_path),
        Corpus(tmp_path / "missing.jsonl"),
        ScriptedClient(reply),
        query=engine.query,
    )

    opportunity = answer["result"]["project_profile"]["suggested"][0]
    assert opportunity["methodology"] == "M-3"
    assert opportunity["solution"] == "S-3"
    assert opportunity["runnable"] is True
    assert answer["result"]["defects"] == []
    assert engine.calls[0] == ("list_methodologies", {})
    assert ("analyze_dataset", {"dataset": "customers"}) in engine.calls
    assert ("plan_features", {"dataset": "customers", "methodology": "M-3"}) in engine.calls


def test_unsupported_suggestion_is_declined_with_its_issue(tmp_path):
    engine = FakeEngine([SEGMENTATION, NEXT_BEST_ACTION])
    reply = json.dumps({
        "suggested": [{
            "intent": "choose the next action",
            "solution": "S-7",
            "methodology": "M-7",
            "runnable": True,
            "why_this_data_supports_it": "customers has account attributes",
        }],
        "declined": [],
    })
    answer = service.run_turn(
        {"profile": CUSTOMERS_WITHOUT_LABEL},
        settings(tmp_path),
        Corpus(tmp_path / "missing.jsonl"),
        ScriptedClient(reply),
        query=engine.query,
    )

    profile = answer["result"]["project_profile"]
    assert profile["suggested"] == []
    declined = profile["declined"][0]
    assert declined["methodology"] == "M-7"
    assert declined["solution"] == "S-7"
    assert declined["runnable"] is False
    assert declined["reason"] == "needs M-7 (not supported yet, zetta#1042)"
    assert answer["result"]["defects"] == []


def test_old_engine_fallback_allows_only_m1(tmp_path):
    calls: list[tuple[str, dict]] = []

    def old_engine(capability, inputs):
        calls.append((capability, inputs))
        raise workspace.CapabilityError("unknown_capability", "not available")

    reply = json.dumps({
        "suggested": [{
            "intent": "predict churn",
            "solution": "S-1",
            "methodology": "M-1",
            "runnable": True,
            "why_this_data_supports_it": "accounts.churned is a label",
        }],
        "declined": [],
    })
    answer = service.run_turn(
        {"profile": PROFILE},
        settings(tmp_path),
        Corpus(tmp_path / "missing.jsonl"),
        ScriptedClient(reply),
        query=old_engine,
    )

    opportunity = answer["result"]["project_profile"]["suggested"][0]
    assert opportunity["methodology"] == "M-1"
    assert opportunity["runnable"] is True
    assert answer["result"]["defects"] == []
    assert calls == [("list_methodologies", {})]


def test_old_engine_fallback_declines_every_methodology_except_m1(tmp_path):
    def old_engine(capability, inputs):
        raise workspace.CapabilityError("unknown_capability", "not available")

    reply = json.dumps({
        "suggested": [{
            "intent": "segment customers",
            "solution": "S-3",
            "methodology": "M-3",
            "runnable": True,
            "why_this_data_supports_it": "customers has country and lifetime_spend",
        }],
        "declined": [],
    })
    answer = service.run_turn(
        {"profile": CUSTOMERS_WITHOUT_LABEL},
        settings(tmp_path),
        Corpus(tmp_path / "missing.jsonl"),
        ScriptedClient(reply),
        query=old_engine,
    )

    profile = answer["result"]["project_profile"]
    assert profile["suggested"] == []
    assert profile["declined"][0]["runnable"] is False
    assert profile["declined"][0]["reason"] == "needs M-3 (not supported yet)"
    assert answer["result"]["defects"] == []


def test_event_logs_are_declined_with_the_required_transform(tmp_path):
    engine = FakeEngine(
        [SEGMENTATION],
        analyses={"events": {"grain": "event", "keys": ["account_id", "occurred_at"]}},
        plans={("events", "M-3"): {
            "status": "transform_first",
            "reason": "aggregate events to one row per account_id and cutoff",
        }},
    )
    reply = json.dumps({
        "suggested": [{
            "intent": "segment accounts",
            "solution": "S-3",
            "methodology": "M-3",
            "runnable": True,
            "why_this_data_supports_it": "events has account_id and event_name",
        }],
        "declined": [],
    })
    answer = service.run_turn(
        {"profile": EVENT_LOGS},
        settings(tmp_path),
        Corpus(tmp_path / "missing.jsonl"),
        ScriptedClient(reply),
        query=engine.query,
    )

    profile = answer["result"]["project_profile"]
    assert profile["suggested"] == []
    declined = profile["declined"][0]
    assert declined["answer"] == "transform first"
    assert declined["runnable"] is True
    assert declined["reason"] == (
        "transform first: aggregate events to one row per account_id and cutoff"
    )
    assert answer["result"]["defects"] == []


def test_workspace_query_uses_lakes_capability_envelope():
    sent = []

    def post(url, token, body):
        sent.append((url, token, body))
        return {"ok": True, "value": {"methodologies": [SEGMENTATION]}}

    query = workspace.make_query("http://lake/", "lat.secret", "customer-1", post)
    answer = query("list_methodologies", {})

    assert answer == {"methodologies": [SEGMENTATION]}
    assert sent == [(
        "http://lake/v1/workspace_query",
        "lat.secret",
        {"customer_id": "customer-1", "capability": "list_methodologies", "input": {}},
    )]
