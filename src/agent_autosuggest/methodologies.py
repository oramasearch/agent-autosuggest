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
    """Render current engine facts for the model without translating or abbreviating them."""
    return (
        "\n\n## Current workspace methodology and feature evidence\n\n"
        "This JSON was read for this turn. It overrides worked examples and general knowledge.\n\n"
        "```json\n"
        + json.dumps(evidence, indent=2, sort_keys=True)
        + "\n```\n"
    )


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


def _transform_instruction(plan: dict[str, Any]) -> str | None:
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
    if status.replace(" ", "_") == "transform_first":
        return str(plan.get("reason") or plan.get("message") or plan.get("transform") or
                   "complete the required data transform")
    return None
