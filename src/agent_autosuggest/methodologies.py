"""Runtime methodology and feature evidence from the workspace engine.

The registry is authoritative. It changes as Zetta gains implementations, so Auto-Suggest reads
it for every turn instead of carrying a portfolio snapshot in this repository.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agent_autosuggest import workspace

Query = Callable[[str, dict[str, Any]], dict[str, Any]]

OLD_ENGINE_METHODOLOGY = {
    "id": "M-1",
    "name": "Churn / GBDT classification",
    "family": "classification",
    "model_kinds": ["gbdt"],
    "solutions": [],
    "inputs": [],
    "outputs": [],
    "metrics": [],
    "runnable": True,
    "partial": False,
}


def inspect(profile: dict[str, Any], query: Query | None) -> dict[str, Any]:
    """Read the registry and the data evidence needed to judge this turn."""
    if query is None:
        return _old_engine("workspace_query is unavailable for this turn")

    try:
        listed = query("list_methodologies", {})
    except workspace.CapabilityError as error:
        if error.code == "unknown_capability":
            return _old_engine("list_methodologies is unknown to this workspace")
        raise

    methods = listed.get("methodologies")
    if not isinstance(methods, list):
        raise workspace.CapabilityError(
            "invalid_response",
            "list_methodologies did not return a methodologies list",
        )

    evidence: dict[str, Any] = {
        "source": "list_methodologies",
        "methodologies": methods,
        "analyses": [],
        "feature_plans": [],
    }
    datasets = _datasets(profile)
    for dataset in datasets:
        analysis = _optional_query(query, "analyze_dataset", {"dataset": dataset})
        evidence["analyses"].append({"dataset": dataset, "analysis": analysis})

    runnable = [method for method in methods if method.get("runnable")]
    for method in runnable:
        methodology = method.get("id")
        if not methodology:
            continue
        for dataset in datasets:
            feature_plan = _optional_query(
                query,
                "plan_features",
                {"dataset": dataset, "methodology": methodology},
            )
            evidence["feature_plans"].append(
                {
                    "dataset": dataset,
                    "methodology": methodology,
                    "plan": feature_plan,
                }
            )
    return evidence


def enforce(answer: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Attach registry status and move opportunities the engine cannot run to declined."""
    methods = {
        method.get("id"): method
        for method in evidence.get("methodologies", [])
        if method.get("id")
    }
    old_engine = evidence.get("source") == "old-engine fallback"
    suggested: list[dict[str, Any]] = []
    declined: list[dict[str, Any]] = []

    for original in answer.get("suggested") or []:
        item = dict(original)
        methodology = item.get("methodology")
        method = methods.get(methodology)
        if method is None:
            item["runnable"] = False
            declined.append(_unsupported(item, {"id": methodology}))
            continue

        item["runnable"] = bool(method.get("runnable"))
        if not item["runnable"]:
            declined.append(_unsupported(item, method))
            continue

        transform = _required_transform(evidence, methodology)
        if transform:
            declined.append(_transform_first(item, transform))
            continue
        suggested.append(item)

    for original in answer.get("declined") or []:
        item = dict(original)
        method = methods.get(item.get("methodology"))
        if method is None:
            if old_engine and item.get("methodology") != "M-1":
                item["runnable"] = False
                item = _unsupported(item, {"id": item.get("methodology")})
            declined.append(item)
            continue
        item["runnable"] = bool(method.get("runnable"))
        if not item["runnable"]:
            declined.append(_unsupported(item, method))
            continue
        transform = _required_transform(evidence, str(item.get("methodology")))
        if transform:
            declined.append(_transform_first(item, transform))
            continue
        declined.append(item)

    return {**answer, "suggested": suggested, "declined": declined}


def as_context(evidence: dict[str, Any]) -> str:
    """Render current engine facts for the model without translating or abbreviating them.

    Each feature plan repeats its dataset's whole analysis, which is already listed once under
    `analyses`; the copy is left out so the plans' status, inferred cutoff and transforms stay
    readable (lake#1153).
    """
    shown = dict(evidence)
    if isinstance(evidence.get("feature_plans"), list):
        shown["feature_plans"] = [_without_analysis(entry) for entry in evidence["feature_plans"]]
    return (
        "\n\n## Current workspace methodology and feature evidence\n\n"
        "This JSON was read for this turn. It overrides worked examples and general knowledge.\n\n"
        "```json\n"
        + json.dumps(shown, indent=2, sort_keys=True)
        + "\n```\n"
    )


def _without_analysis(entry: Any) -> Any:
    if not isinstance(entry, dict) or not isinstance(entry.get("plan"), dict):
        return entry
    plan = {key: value for key, value in entry["plan"].items() if key != "analysis"}
    return {**entry, "plan": plan}


def _old_engine(reason: str) -> dict[str, Any]:
    return {
        "source": "old-engine fallback",
        "reason": reason,
        "methodologies": [dict(OLD_ENGINE_METHODOLOGY)],
        "analyses": [],
        "feature_plans": [],
    }


def _datasets(profile: dict[str, Any]) -> list[str]:
    datasets: list[str] = []
    for table in profile.get("tables") or []:
        name = table.get("name") if isinstance(table, dict) else None
        if name and name not in datasets:
            datasets.append(name)
    return datasets


def _optional_query(query: Query, capability: str, inputs: dict[str, Any]) -> dict[str, Any]:
    try:
        return query(capability, inputs)
    except workspace.CapabilityError as error:
        return {"unavailable": error.code, "message": str(error)}


def _unsupported(item: dict[str, Any], method: dict[str, Any]) -> dict[str, Any]:
    methodology = method.get("id") or item.get("methodology") or "unknown methodology"
    issue = method.get("issue")
    issue_text = f", {issue}" if issue else ""
    return {
        **item,
        "reason": f"needs {methodology} (not supported yet{issue_text})",
        "answer": "not supported yet",
    }


def _transform_first(item: dict[str, Any], transform: str) -> dict[str, Any]:
    instruction = transform.strip()
    if instruction.lower().startswith("transform first"):
        reason = instruction
    else:
        reason = f"transform first: {instruction}"
    return {**item, "reason": reason, "answer": "transform first"}


def _required_transform(evidence: dict[str, Any], methodology: str) -> str | None:
    plans = [
        entry.get("plan") or {}
        for entry in evidence.get("feature_plans", [])
        if entry.get("methodology") == methodology
    ]
    if not plans:
        return None
    instructions = [_transform_instruction(plan) for plan in plans]
    if any(instruction is None for instruction in instructions):
        return None
    return "; ".join(dict.fromkeys(instruction for instruction in instructions if instruction))


def required_transform(evidence: dict[str, Any], methodology_ids: list[str]) -> str | None:
    """Return the preparation that blocks any selected methodology in a composition."""
    instructions = [
        instruction
        for methodology in methodology_ids
        if (instruction := _required_transform(evidence, methodology))
    ]
    if not instructions:
        return None
    return "; ".join(dict.fromkeys(instructions))


# The Lake capabilities that perform each preparation the engine names, for a lake that does not
# answer `performed_by` itself (lake#1153, lake docs/api/feature-planning.md).
_PERFORMED_BY = {
    "add_snapshot_date": [
        {"capability": "plan_features",
         "does": "declare the date the rows were observed at as as_of"},
        {"capability": "upload_dataset",
         "does": "or load the table again with an as_of column"},
    ],
    "aggregate_events_to_entity": [
        {"capability": "workspace_sql", "does": "run the proposed SELECT for one row per entity"},
        {"capability": "upload_dataset", "does": "load the per-entity rows as a new dataset"},
    ],
}
_LOAD_AGAIN = [{"capability": "upload_dataset",
                "does": "load the prepared table as a new dataset, then plan that one"}]

# Every Lake capability a transform-first answer may name as the one that performs it.
LAKE_TRANSFORM_CAPABILITIES = (
    "plan_features", "upload_dataset", "workspace_sql", "create_dataset_from_query",
    "generate_dataset",
)


def performed_by(transform: dict[str, Any]) -> list[dict[str, Any]]:
    """The Lake capabilities that perform an engine transform, as lake names them or by default."""
    steps = transform.get("performed_by")
    if isinstance(steps, list) and steps:
        return [step for step in steps if isinstance(step, dict) and step.get("capability")]
    return _PERFORMED_BY.get(str(transform.get("name") or ""), _LOAD_AGAIN)


def _with_capability(text: str, steps: list[dict[str, Any]]) -> str:
    rendered = "; ".join(
        f"{step['capability']} ({step['does']})" if step.get("does") else str(step["capability"])
        for step in steps
    )
    return f"{text.strip().rstrip('.')}. Lake does it with {rendered}"


def _engine_transform(transform: dict[str, Any]) -> str:
    """zetta's `transform_required` {name, description, ...}, with what performs it in Lake."""
    name = str(transform.get("name") or "a data transform")
    description = str(transform.get("description") or "").strip()
    text = f"{name}: {description}" if description else name
    why = transform.get("why_no_as_of")
    if why:
        text = f"{text.rstrip('.')} (no cutoff was read from the data: {why})"
    return _with_capability(text, performed_by(transform))


def _transform_instruction(plan: dict[str, Any]) -> str | None:
    engine = plan.get("transform_required")
    if isinstance(engine, dict) and engine:
        return _engine_transform(engine)
    instruction = _legacy_transform_instruction(plan)
    if instruction is None:
        return None
    if any(capability in instruction for capability in LAKE_TRANSFORM_CAPABILITIES):
        return instruction
    return _with_capability(instruction, _LOAD_AGAIN)


def ready_dataset(evidence: dict[str, Any], methodology_ids: list[str]) -> dict[str, Any] | None:
    """A dataset every selected methodology plans `ready` on, with its plans; None when none does.

    The feature plans are the engine's judgement of whether the data is ready (lake#1153): a
    composition they all plan on needs no transform first.
    """
    if not methodology_ids:
        return None
    plans: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in evidence.get("feature_plans", []):
        plan = entry.get("plan") if isinstance(entry, dict) else None
        if not isinstance(plan, dict) or str(plan.get("status") or "").lower() != "ready":
            continue
        plans.setdefault(str(entry.get("dataset")), {})[str(entry.get("methodology"))] = plan
    for dataset, by_methodology in plans.items():
        if all(methodology in by_methodology for methodology in methodology_ids):
            return {"dataset": dataset,
                    "plans": [by_methodology[methodology] for methodology in methodology_ids]}
    return None


def _legacy_transform_instruction(plan: dict[str, Any]) -> str | None:
    transform = plan.get("transform_first")
    if isinstance(transform, str) and transform.strip():
        return transform
    if isinstance(transform, dict):
        return str(transform.get("reason") or transform.get("message") or
                   transform.get("instruction") or "complete the required data transform")
    if isinstance(transform, list) and transform:
        return "; ".join(str(step) for step in transform)
    if transform:
        return str(plan.get("reason") or plan.get("message") or plan.get("transform") or
                   "complete the required data transform")
    status = str(plan.get("status") or plan.get("answer") or "").lower().replace("-", "_")
    if status.replace(" ", "_") in {"transform_first", "transform_required"}:
        return str(plan.get("reason") or plan.get("message") or plan.get("transform") or
                   "complete the required data transform")
    return None
