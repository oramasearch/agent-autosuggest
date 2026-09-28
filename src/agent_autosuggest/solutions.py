"""Runtime solution catalogue from the workspace engine.

The build catalogue remains useful vocabulary for engines that predate solutions. Once
``list_solutions`` exists, its records are authoritative and are read again for every turn.
"""

from __future__ import annotations

import json
from typing import Any

from agent_autosuggest import methodologies, workspace

Query = methodologies.Query


def inspect(query: Query | None) -> dict[str, Any]:
    """Read the solution registry for this turn, or identify a legacy engine."""
    if query is None:
        return _fallback("workspace_query is unavailable for this turn")

    try:
        listed = query("list_solutions", {})
    except workspace.CapabilityError as error:
        if error.code == "unknown_capability":
            return _fallback("list_solutions is unknown to this workspace")
        raise

    entries = listed.get("solutions")
    if not isinstance(entries, list):
        raise workspace.CapabilityError(
            "invalid_response",
            "list_solutions did not return a solutions list",
        )
    return {
        "source": "list_solutions",
        "solutions": entries,
        "details": {},
    }


def is_current(evidence: dict[str, Any]) -> bool:
    """Whether this turn can use catalogue solutions instead of the legacy contract."""
    return evidence.get("source") == "list_solutions"


def enforce(
    answer: dict[str, Any],
    evidence: dict[str, Any],
    methodology_evidence: dict[str, Any],
    query: Query | None,
) -> dict[str, Any]:
    """Attach registry facts and decline solutions that cannot operate on this data."""
    if not is_current(evidence):
        return methodologies.enforce(answer, methodology_evidence)

    suggested: list[dict[str, Any]] = []
    declined: list[dict[str, Any]] = []
    for original in answer.get("suggested") or []:
        item = _ground(dict(original), evidence, methodology_evidence, query)
        if item.get("answer"):
            declined.append(item)
        else:
            suggested.append(item)

    for original in answer.get("declined") or []:
        item = _ground(dict(original), evidence, methodology_evidence, query)
        declined.append(item)

    return {**answer, "suggested": suggested, "declined": declined}


def as_context(evidence: dict[str, Any]) -> str:
    """Render the live registry without replacing its product language."""
    if not is_current(evidence):
        return (
            "\n\n## Current workspace solution registry\n\n"
            "`list_solutions` is not available. Use the legacy methodology contract below.\n"
        )
    visible = {key: value for key, value in evidence.items() if key != "details"}
    return (
        "\n\n## Current workspace solution registry\n\n"
        "This JSON was read for this turn and is authoritative. Product copy and runtime status "
        "must not be strengthened.\n\n"
        "```json\n"
        + json.dumps(visible, indent=2, sort_keys=True)
        + "\n```\n"
    )


def by_id(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the most detailed record available for each registry id."""
    records = {
        entry.get("id"): entry
        for entry in evidence.get("solutions", [])
        if isinstance(entry, dict) and entry.get("id")
    }
    details = evidence.get("details") or {}
    records.update(
        {
            identifier: entry
            for identifier, entry in details.items()
            if isinstance(entry, dict)
        }
    )
    return records


def available_levels(solution: dict[str, Any]) -> list[int]:
    """Read level integers from either deployed spelling of the registry field."""
    raw = solution.get("capability_levels")
    if raw is None:
        raw = solution.get("levels")
    levels: list[int] = []
    for entry in raw or []:
        value = entry.get("level") if isinstance(entry, dict) else entry
        if isinstance(value, int) and not isinstance(value, bool) and value not in levels:
            levels.append(value)
    return levels


def selected_methodologies(solution: dict[str, Any], level: Any) -> list[str]:
    """Return the public composition selected at a catalogue level."""
    if not isinstance(level, int) or isinstance(level, bool):
        return []
    selected: list[str] = []
    for entry in solution.get("composition") or []:
        if isinstance(entry, str):
            methodology = entry
            minimum_level = 1
        elif isinstance(entry, dict):
            methodology = entry.get("methodology") or entry.get("id")
            minimum_level = entry.get("minimum_level", 1)
        else:
            continue
        if (
            methodology
            and isinstance(minimum_level, int)
            and not isinstance(minimum_level, bool)
            and minimum_level <= level
        ):
            selected.append(str(methodology))
    return selected


def missing_components(solution: dict[str, Any]) -> list[dict[str, Any]]:
    """Read missing production components from the registry's deployed spellings."""
    raw = solution.get("missing_components")
    if raw is None:
        raw = solution.get("missing")
    return [entry for entry in raw or [] if isinstance(entry, dict)]


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "source": "old-engine fallback",
        "reason": reason,
        "solutions": [],
        "details": {},
    }


def _ground(
    item: dict[str, Any],
    evidence: dict[str, Any],
    methodology_evidence: dict[str, Any],
    query: Query | None,
) -> dict[str, Any]:
    identifier = item.get("solution")
    solution = _get_solution(str(identifier), evidence, query) if identifier else None
    if solution is None:
        item["runnable"] = False
        return _unsupported(item)

    level = item.get("level")
    methodology_ids = selected_methodologies(solution, level)
    item["methodologies"] = methodology_ids
    item.pop("methodology", None)
    item["runnable"] = bool(solution.get("runnable"))

    if solution.get("status") == "sketch":
        return _sketch(item, solution)
    if not item["runnable"]:
        return _not_runnable(item, solution)

    transform = methodologies.required_transform(methodology_evidence, methodology_ids)
    if transform:
        return _transform_first(item, transform)
    return item


def _get_solution(
    identifier: str,
    evidence: dict[str, Any],
    query: Query | None,
) -> dict[str, Any] | None:
    known = by_id(evidence)
    listed = known.get(identifier)
    if listed is None:
        return None
    details = evidence.setdefault("details", {})
    if identifier in details:
        detail = details[identifier]
        return detail if isinstance(detail, dict) else listed
    if query is None:
        details[identifier] = listed
        return listed

    try:
        response = query("get_solution", {"solution": identifier})
    except workspace.CapabilityError as error:
        if error.code == "unknown_capability":
            details[identifier] = listed
            return listed
        raise
    detail = response.get("solution")
    if not isinstance(detail, dict):
        raise workspace.CapabilityError(
            "invalid_response",
            "get_solution did not return a solution object",
        )
    details[identifier] = detail
    return detail


def _unsupported(item: dict[str, Any]) -> dict[str, Any]:
    identifier = item.get("solution") or "unknown solution"
    return {
        **item,
        "reason": f"needs {identifier} (not supported by this solution registry)",
        "answer": "not supported yet",
    }


def _sketch(item: dict[str, Any], solution: dict[str, Any]) -> dict[str, Any]:
    identifier = solution.get("id") or item.get("solution") or "unknown solution"
    promotion = solution.get("promotion_condition") or "the catalogue promotion condition"
    issue = solution.get("issue")
    issue_text = f"; {issue}" if issue else ""
    return {
        **item,
        "reason": (
            f"{identifier} is a sketch and cannot be created; "
            f"promotion condition: {promotion}{issue_text}"
        ),
        "answer": "not supported yet",
    }


def _not_runnable(item: dict[str, Any], solution: dict[str, Any]) -> dict[str, Any]:
    missing = missing_components(solution)
    needs = []
    for component in missing:
        name = component.get("name") or component.get("id") or "a production component"
        issue = component.get("issue")
        needs.append(f"{name} ({issue})" if issue else str(name))
    if needs:
        reason = "the pipeline builds; production operation needs " + ", ".join(needs)
    else:
        identifier = solution.get("id") or item.get("solution") or "unknown solution"
        issue = solution.get("issue")
        issue_text = f", {issue}" if issue else ""
        reason = f"needs {identifier} (not supported yet{issue_text})"
    return {**item, "reason": reason, "answer": "not supported yet"}


def _transform_first(item: dict[str, Any], transform: str) -> dict[str, Any]:
    instruction = transform.strip()
    if instruction.lower().startswith("transform first"):
        reason = instruction
    else:
        reason = f"transform first: {instruction}"
    return {**item, "reason": reason, "answer": "transform first"}
