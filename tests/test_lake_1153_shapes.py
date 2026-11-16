# SPDX-License-Identifier: MIT

"""lake#1153: Scan my workspace suggested nothing on clean churn tables.

Each dataset shape the issue lists, with the evidence a lake that has lake#1153 answers for it, and
the answer the model gave on lake-dev (prompt v4): every shape declined as `transform first`. The
feature plans now decide: a composition that plans `ready` is an Opportunity with the preparation as
a step of the project, and a real `transform_required` is declined naming the Lake capability that
performs it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_autosuggest import catalog, methodologies, service, solutions, validate
from agent_autosuggest.retrieval import Corpus

CHURN = {
    "id": "S-1",
    "name": "Churn & Retention",
    "composition": [
        {"methodology": "M-1", "minimum_level": 1, "role": "primary"},
        {"methodology": "M-2", "minimum_level": 1, "role": "foundation"},
        {"methodology": "M-3", "minimum_level": 1, "role": "foundation"},
        {"methodology": "M-5", "minimum_level": 2, "role": "attachable"},
    ],
    "levels": [{"level": 1, "name": "CL-1"}, {"level": 2, "name": "CL-2"}],
    "required_data_roles": [
        {"role": "entity", "required": True, "description": "customer, account, or household"},
        {"role": "label", "required": True, "description": "churn event and outcome window"},
        {"role": "time", "required": True, "description": "feature cutoff and label window"},
    ],
    "missing_components": [],
    "status": "catalogue",
    "runnable": True,
    "issue": "",
    "promotion_condition": "",
}
METHODOLOGIES = [{"id": m, "runnable": True} for m in ("M-1", "M-2", "M-3", "M-5")]


def ready(as_of_column: str | None = None) -> dict:
    plan = {"status": "ready", "entries": [], "excluded": [], "transform_required": None,
            "analysis": {"columns": ["...the whole analysis..."]}}
    if as_of_column:
        plan["inferred"] = {"as_of": {"column": as_of_column, "value": "2026-09-27",
                                      "reason": "the latest snapshot date"}}
    return plan


SNAPSHOT_MISSING = {
    "status": "transform_required",
    "entries": [],
    "excluded": [],
    "transform_required": {
        "name": "add_snapshot_date",
        "description": "Datetime features must be computed relative to a declared as_of or "
                       "snapshot date.",
        "why_no_as_of": "no column of datasets.accounts is named as each row's snapshot date",
        "performed_by": [
            {"capability": "plan_features",
             "does": "declare the date these rows were observed at as as_of"},
            {"capability": "upload_dataset", "does": "or load the table again with an as_of column"},
        ],
    },
}


def table(name: str, *columns: str) -> dict:
    return {"database": "datasets", "name": name,
            "columns": [{"name": column, "type": "string"} for column in columns]}


# The issue's shapes, each with the plans lake answers and the decline the model gave on lake-dev.
SHAPES = {
    "no date": (
        table("churn", "account", "tenure_months", "monthly_spend", "support_tickets",
              "churn_risk", "churned"),
        None,
        "transform first: S-1 requires a time role for feature cutoff and label window, and this "
        "dataset has no observation timestamp",
    ),
    "observed_at and a window": (
        table("churn", "account", "tenure_months", "monthly_spend", "support_tickets",
              "churn_risk", "churned", "observed_at", "churn_window_days"),
        "observed_at",
        "transform first: add an explicit prediction snapshot/as_of cutoff for each account row; "
        "clean the monthly_spend sentinel value 999",
    ),
    "as_of, a window and a risk table": (
        table("churn", "account", "as_of", "churn_window_days", "tenure_months",
              "monthly_spend", "support_tickets", "churned"),
        "as_of",
        "transform first: the feature plan for M-1 and M-3 requires an approved snapshot/as_of "
        "setup before temporal features are computed",
    ),
}


class Engine:
    def __init__(self, plans: dict) -> None:
        self.plans = plans
        self.calls: list[tuple[str, dict]] = []

    def query(self, capability: str, inputs: dict) -> dict:
        self.calls.append((capability, inputs))
        if capability == "list_methodologies":
            return {"methodologies": METHODOLOGIES}
        if capability == "analyze_dataset":
            return {"grain": "entity"}
        if capability == "plan_features":
            return self.plans[(inputs["dataset"], inputs["methodology"])]
        if capability == "list_solutions":
            return {"solutions": [CHURN]}
        if capability == "get_solution":
            return {"solution": CHURN}
        raise AssertionError(f"unexpected capability {capability}")


class ScriptedClient:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **arguments):
        self.calls.append(arguments)
        content = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture(autouse=True)
def built_catalogue(tmp_path, monkeypatch):
    built = tmp_path / "catalog.json"
    built.write_text(json.dumps({"solutions": [{"id": "S-1", "name": "Churn"}]}))
    monkeypatch.setenv("ZETTA_CATALOG", str(built))
    catalog.load.cache_clear()
    yield
    catalog.load.cache_clear()


def settings(tmp_path):
    return SimpleNamespace(trace_dir=tmp_path / "traces", model="test-model", shots=0,
                           lake_url="", token="")


def declined_by_the_model(reason: str) -> str:
    return json.dumps({"suggested": [], "declined": [{
        "intent": "predict which accounts churn in the next window",
        "solution": "S-1", "level": 1, "methodologies": ["M-1", "M-2", "M-3"],
        "runnable": True, "reason": reason, "answer": "transform first"}]})


@pytest.mark.parametrize("shape", SHAPES, ids=list(SHAPES))
def test_each_shape_from_the_issue_is_an_opportunity(tmp_path, shape):
    churn, as_of_column, reason = SHAPES[shape]
    plans = {("churn", m): ready(as_of_column) for m in ("M-1", "M-2", "M-3", "M-5")}
    tables = [churn]
    if shape.endswith("risk table"):
        tables.append(table("ux_review_at_risk", "account", "risk"))
        # The risk table has no label: it is not the one the composition plans on.
        for methodology in ("M-1", "M-2", "M-3", "M-5"):
            plans[("ux_review_at_risk", methodology)] = {
                "status": "transform_required",
                "transform_required": {"name": "capture_label", "description": "no label"}}
    engine = Engine(plans)
    answer = service.run_turn(
        {"profile": {"industry": "saas", "persona": "customer success", "tables": tables}},
        settings(tmp_path), Corpus(tmp_path / "missing.jsonl"),
        ScriptedClient(declined_by_the_model(reason)), query=engine.query)

    assert answer["ok"], answer
    profile = answer["result"]["project_profile"]
    assert profile["declined"] == []
    [opportunity] = profile["suggested"]
    assert opportunity["solution"] == "S-1"
    assert opportunity["methodologies"] == ["M-1", "M-2", "M-3"]
    assert opportunity["runnable"] is True
    # The model's preparation is a step of the project, not a reason to suggest nothing.
    assert opportunity["required_first"] == [reason.removeprefix("transform first:").strip()]
    assert opportunity["why_this_data_supports_it"].startswith(
        "churn: the feature plans for M-1, M-2, M-3 are ready")
    if as_of_column:
        assert f"as of {as_of_column} (2026-09-27)" in opportunity["why_this_data_supports_it"]
    assert answer["result"]["defects"] == []
    assert answer["result"]["prompt_version"] == "v5"


def test_a_date_that_is_not_a_snapshot_is_declined_with_the_capability_that_fixes_it(tmp_path):
    plans = {("accounts", m): SNAPSHOT_MISSING for m in ("M-1", "M-2", "M-3", "M-5")}
    plans[("accounts", "M-2")] = ready()
    engine = Engine(plans)
    suggested = json.dumps({"suggested": [{
        "intent": "predict churn", "solution": "S-1", "level": 1,
        "methodologies": ["M-1", "M-2", "M-3"], "runnable": True,
        "why_this_data_supports_it": "accounts has account, signup_date and churned"}],
        "declined": []})
    answer = service.run_turn(
        {"profile": {"tables": [table("accounts", "account", "signup_date", "churned")]}},
        settings(tmp_path), Corpus(tmp_path / "missing.jsonl"), ScriptedClient(suggested),
        query=engine.query)

    profile = answer["result"]["project_profile"]
    assert profile["suggested"] == []
    [declined] = profile["declined"]
    assert declined["answer"] == "transform first"
    assert declined["reason"].startswith("transform first: add_snapshot_date: ")
    assert "plan_features (declare the date these rows were observed at as as_of)" in (
        declined["reason"])
    assert "upload_dataset" in declined["reason"]
    assert "no column of datasets.accounts" in declined["reason"]
    assert answer["result"]["defects"] == []


def test_a_transform_first_that_names_no_lake_capability_is_sent_back(tmp_path):
    # No plan answered (an unreadable dataset): the model's own decline must still say how.
    plans = {("accounts", m): {"unavailable": "store_error"}
             for m in ("M-1", "M-2", "M-3", "M-5")}
    vague = declined_by_the_model(
        "transform first: create and approve a clean prediction/training dataset")
    fixed = declined_by_the_model(
        "transform first: load accounts again with an as_of column using upload_dataset")
    client = ScriptedClient(vague, fixed)
    answer = service.run_turn(
        {"profile": {"tables": [table("accounts", "account", "churned")]}},
        settings(tmp_path), Corpus(tmp_path / "missing.jsonl"), client,
        query=Engine(plans).query)

    assert len(client.calls) == 2
    feedback = client.calls[1]["messages"][-1]["content"]
    assert "must name the Lake capability that performs it" in feedback
    assert answer["result"]["project_profile"]["declined"][0]["reason"].endswith(
        "using upload_dataset")
    assert answer["result"]["defects"] == []


def test_feature_plans_reach_the_model_without_their_repeated_analysis():
    evidence = {"source": "list_methodologies", "methodologies": METHODOLOGIES,
                "analyses": [{"dataset": "churn", "analysis": {"grain": "entity"}}],
                "feature_plans": [{"dataset": "churn", "methodology": "M-1",
                                   "plan": ready("as_of")}]}
    context = methodologies.as_context(evidence)
    assert "the whole analysis" not in context
    assert '"inferred"' in context
    assert '"grain": "entity"' in context
    assert evidence["feature_plans"][0]["plan"]["analysis"], "the evidence itself is kept whole"


def test_a_ready_dataset_needs_every_selected_methodology():
    evidence = {"feature_plans": [
        {"dataset": "a", "methodology": "M-1", "plan": ready()},
        {"dataset": "b", "methodology": "M-3", "plan": ready()},
        {"dataset": "a", "methodology": "M-3", "plan": SNAPSHOT_MISSING},
    ]}
    assert methodologies.ready_dataset(evidence, ["M-1", "M-3"]) is None
    assert methodologies.ready_dataset(evidence, ["M-1"])["dataset"] == "a"
    assert methodologies.ready_dataset(evidence, []) is None
    solution_evidence = {"source": "list_solutions", "solutions": [CHURN], "details": {}}
    declined = {"suggested": [], "declined": [{
        "intent": "churn", "solution": "S-1", "level": 1, "methodologies": ["M-1", "M-2", "M-3"],
        "runnable": True, "answer": "transform first",
        "reason": "transform first: upload_dataset with an as_of column"}]}
    kept = solutions.enforce(declined, solution_evidence, evidence, None)
    assert kept["suggested"] == [] and kept["declined"][0]["answer"] == "transform first"
    assert validate.defects(kept, evidence, solution_evidence) == []
