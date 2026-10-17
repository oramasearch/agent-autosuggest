"""Service mode without a model: the turn contract, the health rule, and the trace."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_autosuggest import apps, catalog, service, workspace
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
        self.reply = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **arguments):
        self.calls.append(arguments)
        return self.reply


class FakeEngine:
    """A lake/Zetta read client with a current registry and per-dataset evidence."""

    def __init__(self, methodologies, analyses=None, plans=None, solution_entries=None):
        self.methodologies = methodologies
        self.analyses = analyses or {}
        self.plans = plans or {}
        self.solution_entries = solution_entries
        self.calls: list[tuple[str, dict]] = []

    def query(self, capability, inputs):
        self.calls.append((capability, inputs))
        if capability == "list_methodologies":
            return {"methodologies": self.methodologies}
        if capability == "analyze_dataset":
            return self.analyses.get(inputs["dataset"], {})
        if capability == "plan_features":
            return self.plans.get((inputs["dataset"], inputs["methodology"]), {"ready": True})
        if capability == "list_solutions":
            if self.solution_entries is None:
                raise workspace.CapabilityError("unknown_capability", "not deployed")
            return {"solutions": self.solution_entries}
        if capability == "get_solution":
            solution = next(
                entry
                for entry in self.solution_entries or []
                if entry["id"] == inputs["solution"]
            )
            return {"solution": solution}
        raise AssertionError(f"unexpected capability {capability}")


class FakeLakeState:
    """The direct lake App/build read surface handed to one Auto-Suggest turn."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def query(self, capability, inputs):
        self.calls.append((capability, inputs))
        if capability == "list_apps":
            return {"apps": [{"id": "app-1", "name": "revenue-dashboard"}]}
        if capability == "get_app":
            return {"id": inputs["app_id"], "name": "revenue-dashboard", "phase": "Running"}
        if capability == "receive_app_events":
            return {"events": [{"sequence": 2, "status": {"phase": "Running"}}],
                    "next_sequence": 3}
        if capability == "list_builds":
            return {"builds": [{"id": "build-1", "phase": "Succeeded"}]}
        if capability == "get_build":
            return {"id": inputs["build_id"], "phase": "Succeeded", "target": "revenue:v1"}
        if capability == "receive_build_events":
            return {"events": [{"sequence": 4, "type": "done"}], "next_sequence": 5}
        raise AssertionError(f"unexpected lake capability {capability}")


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
RUNNABLE_SEGMENTATION_SOLUTION = {
    "id": "S-3",
    "name": "Customer segmentation",
    "promise": "Group customers into useful operating cohorts.",
    "use_case": "Customer planning",
    "composition": [
        {"methodology": "M-3", "role": "primary", "minimum_level": 1},
    ],
    "capability_levels": [
        {"level": 1, "name": "insight", "description": "Describe cohorts"},
    ],
    "required_data_roles": [
        {"role": "entity", "required": True, "description": "customer"},
    ],
    "action_patterns": ["report"],
    "missing_components": [],
    "status": "catalogue",
    "runnable": True,
    "issue": "",
    "promotion_condition": "",
}
MISSING_ACTION_SOLUTION = {
    "id": "S-7",
    "name": "Next best action",
    "promise": "Choose a governed action for each account.",
    "use_case": "Account action planning",
    "composition": [
        {"methodology": "M-7", "role": "primary", "minimum_level": 1},
    ],
    "capability_levels": [
        {"level": 1, "name": "insight", "description": "Recommend actions"},
    ],
    "required_data_roles": [
        {"role": "entity", "required": True, "description": "account"},
    ],
    "action_patterns": ["tasks"],
    "missing_components": [
        {"id": "policy", "name": "decision policy", "issue": "zetta#1104"},
    ],
    "status": "catalogue",
    "runnable": False,
    "issue": "zetta#1116",
    "promotion_condition": "",
}
INDUSTRIAL_SKETCH = {
    "id": "B-1",
    "name": "Industrial optimization",
    "promise": "Coordinate bounded production decisions.",
    "use_case": "Production planning",
    "composition": [
        {"methodology": "M-3", "role": "primary", "minimum_level": 1},
    ],
    "capability_levels": [],
    "required_data_roles": [
        {"role": "entity", "required": True, "description": "production run"},
    ],
    "action_patterns": [],
    "missing_components": [],
    "status": "sketch",
    "runnable": False,
    "issue": "zetta#1140",
    "promotion_condition": "promote with a design customer and safety review",
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


def test_health_reports_the_installed_version(tmp_path):
    from importlib.metadata import version

    corpus_file = tmp_path / "corpus.jsonl"
    corpus_file.write_text(json.dumps({"profile_id": "p1", "industry": "retail",
                                       "tables": [{"name": "orders", "columns": ["id"]}],
                                       "project_profile": {}}) + "\n")
    status, body = service.health(settings(tmp_path), Corpus(corpus_file))
    assert status == 200 and body["version"] == version("agent-autosuggest")


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


def test_solution_registry_suggests_a_runnable_solution_at_a_level(tmp_path):
    engine = FakeEngine(
        [SEGMENTATION],
        analyses={"customers": {"grain": "snapshot", "keys": ["customer_id"]}},
        plans={("customers", "M-3"): {"ready": True}},
        solution_entries=[RUNNABLE_SEGMENTATION_SOLUTION],
    )
    reply = json.dumps({
        "suggested": [{
            "intent": "segment customers",
            "solution": "S-3",
            "level": 1,
            "methodologies": ["M-3"],
            "runnable": True,
            "why_this_data_supports_it": "customers is one row per customer_id",
        }],
        "declined": [],
    })

    client = ScriptedClient(reply)
    answer = service.run_turn(
        {"profile": CUSTOMERS_WITHOUT_LABEL},
        settings(tmp_path),
        Corpus(tmp_path / "missing.jsonl"),
        client,
        query=engine.query,
    )

    opportunity = answer["result"]["project_profile"]["suggested"][0]
    assert opportunity["solution"] == "S-3"
    assert opportunity["level"] == 1
    assert opportunity["methodologies"] == ["M-3"]
    assert opportunity["runnable"] is True
    assert answer["result"]["prompt_version"] == "v4"
    assert answer["result"]["defects"] == []
    assert ("list_solutions", {}) in engine.calls
    assert ("get_solution", {"solution": "S-3"}) in engine.calls
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert RUNNABLE_SEGMENTATION_SOLUTION["use_case"] in system_prompt
    assert '"required_data_roles"' in system_prompt


def test_data_app_opportunity_uses_lake_state_and_stays_a_confirmed_proposal(tmp_path):
    engine = FakeEngine(
        [SEGMENTATION],
        analyses={"customers": {"grain": "snapshot", "keys": ["customer_id"]}},
        plans={("customers", "M-3"): {"ready": True}},
        solution_entries=[RUNNABLE_SEGMENTATION_SOLUTION],
    )
    lake = FakeLakeState()
    reply = json.dumps({
        "suggested": [{
            "intent": "explore customer segments",
            "solution": "S-3",
            "level": 1,
            "methodologies": ["M-3"],
            "runnable": True,
            "why_this_data_supports_it": "customers has country and spend per customer_id",
            "delivery": {
                "kind": "app",
                "shows": "segment size, country mix, spend, and customer drill-downs",
                "agent": "another-agent",
                "turn_kind": "api",
                "status": "created",
                "confirmation_required": False,
                "proposed_actions": ["create_app", "deploy_app", "workspace_act"],
            },
        }],
        "declined": [],
    })
    client = ScriptedClient(reply)

    answer = service.run_turn(
        {"profile": CUSTOMERS_WITHOUT_LABEL},
        settings(tmp_path),
        Corpus(tmp_path / "missing.jsonl"),
        client,
        query=engine.query,
        lake_query=lake.query,
    )

    assert answer["ok"], answer
    delivery = answer["result"]["project_profile"]["suggested"][0]["delivery"]
    assert delivery == {
        "kind": "app",
        "shows": "segment size, country mix, spend, and customer drill-downs",
        "agent": "agent-application",
        "turn_kind": "app",
        "status": "proposed",
        "confirmation_required": True,
        "proposed_actions": ["create_app", "deploy_app"],
    }
    assert answer["result"]["defects"] == []
    assert [capability for capability, _ in lake.calls] == [
        "list_apps",
        "list_builds",
        "get_app",
        "receive_app_events",
        "get_build",
        "receive_build_events",
    ]
    assert all(capability in apps.READ_CAPABILITIES for capability, _ in lake.calls)
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert "revenue-dashboard" in system_prompt
    assert "agent-application" in system_prompt
    trace = json.loads((tmp_path / "traces" / f"{answer['trace_id']}.json").read_text())
    assert trace["app_evidence"]["apps"][0]["app"]["id"] == "app-1"


def test_solution_with_missing_components_is_not_presented_as_ready(tmp_path):
    engine = FakeEngine(
        [NEXT_BEST_ACTION],
        solution_entries=[MISSING_ACTION_SOLUTION],
    )
    reply = json.dumps({
        "suggested": [{
            "intent": "choose the next action",
            "solution": "S-7",
            "level": 1,
            "methodologies": ["M-7"],
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
    assert declined["solution"] == "S-7"
    assert declined["methodologies"] == ["M-7"]
    assert declined["runnable"] is False
    assert declined["answer"] == "not supported yet"
    assert declined["reason"] == (
        "the pipeline builds; production operation needs decision policy (zetta#1104)"
    )
    assert answer["result"]["defects"] == []


def test_a_band_b_sketch_is_refused_with_its_promotion_condition(tmp_path):
    engine = FakeEngine(
        [SEGMENTATION],
        solution_entries=[INDUSTRIAL_SKETCH],
    )
    reply = json.dumps({
        "suggested": [{
            "intent": "optimize production",
            "solution": "B-1",
            "level": 1,
            "methodologies": ["M-3"],
            "runnable": True,
            "why_this_data_supports_it": "customers stands in for production runs",
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
    assert declined["solution"] == "B-1"
    assert declined["runnable"] is False
    assert declined["answer"] == "not supported yet"
    assert "sketch" in declined["reason"]
    assert INDUSTRIAL_SKETCH["promotion_condition"] in declined["reason"]
    assert "zetta#1140" in declined["reason"]
    assert answer["result"]["defects"] == []


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
    assert calls == [("list_methodologies", {}), ("list_solutions", {})]


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


def test_solution_is_declined_when_its_data_requires_a_transform(tmp_path):
    engine = FakeEngine(
        [SEGMENTATION],
        analyses={"events": {"grain": "event", "keys": ["account_id", "occurred_at"]}},
        plans={("events", "M-3"): {
            "status": "transform_first",
            "reason": "aggregate events to one row per account_id and cutoff",
        }},
        solution_entries=[RUNNABLE_SEGMENTATION_SOLUTION],
    )
    reply = json.dumps({
        "suggested": [{
            "intent": "segment accounts",
            "solution": "S-3",
            "level": 1,
            "methodologies": ["M-3"],
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
    assert declined["methodologies"] == ["M-3"]
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


def test_a_turn_calls_its_model_through_lakes_gateway_with_the_turns_key(tmp_path, monkeypatch):
    """lake#248: the model is lake's `/openai/v1`, with the turn's own key, never a provider key."""
    import httpx

    from agent_autosuggest import model

    reply = json.dumps({"suggested": [], "declined": []})
    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "gpt-4o",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(service, "gateway_client", lambda url, token: model.gateway_client(
        url, token, http_client=httpx.Client(transport=httpx.MockTransport(gateway))))
    engine = FakeEngine([])
    answer = service.run_turn(
        {"context": {"profile": PROFILE}, "lake_url": "http://lake:8080",
         "lake_token": "lat.task.secret", "customer_id": "c"},
        settings(tmp_path), Corpus(tmp_path / "missing.jsonl"), None,
        query=engine.query, lake_query=FakeLakeState().query)
    assert answer["ok"], answer
    assert [str(request.url) for request in seen] == ["http://lake:8080/openai/v1/chat/completions"]
    assert seen[0].headers["authorization"] == "Bearer lat.task.secret"

    # No lake credential: the turn is refused rather than sent to a provider directly.
    refused = service.run_turn({"context": {"profile": PROFILE}}, settings(tmp_path),
                               Corpus(tmp_path / "missing.jsonl"), None,
                               query=engine.query, lake_query=FakeLakeState().query)
    assert not refused["ok"] and "lake_token" in refused["error"]
    assert len(seen) == 1
