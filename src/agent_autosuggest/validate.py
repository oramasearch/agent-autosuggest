"""Check solution citations and runtime methodology claims before they reach a caller."""

from __future__ import annotations

import re
from typing import Any

from agent_autosuggest import catalog, methodologies


def defects(answer: dict[str, Any], evidence: dict[str, Any] | None = None) -> list[str]:
    """Everything wrong with this project profile. Empty means it is usable."""
    problems: list[str] = []
    try:
        known = catalog.solution_ids()
    except catalog.Unavailable as error:
        # Not "skip the citation check" — report it. Silently passing every id is how `S-15` got
        # through a validator that returned no defects.
        return [f"cannot check solution ids: {error}"]

    evidence = evidence or methodologies.inspect({}, None)
    methods = {
        method.get("id"): method
        for method in evidence.get("methodologies", [])
        if method.get("id")
    }
    old_engine = evidence.get("source") == "old-engine fallback"

    for index, item in enumerate(answer.get("suggested") or []):
        where = f"suggested[{index}]"
        _check_identity(item, where, known, methods, old_engine, problems)
        if item.get("runnable") is not True:
            problems.append(f"{where}: a suggested opportunity must be runnable")
        if not item.get("why_this_data_supports_it"):
            problems.append(f"{where}: no grounding in the profile's columns")

    for index, item in enumerate(answer.get("declined") or []):
        where = f"declined[{index}]"
        _check_identity(item, where, known, methods, old_engine, problems)
        allowed = {"capture it first", "cannot be done", "not supported yet", "transform first"}
        if item.get("answer") not in allowed:
            problems.append(f"{where}: {item.get('answer')!r} is not a supported decline answer")
        if item.get("answer") == "not supported yet":
            _check_unsupported(item, where, methods, problems)
        if item.get("answer") == "transform first":
            reason = str(item.get("reason") or "")
            if not reason.lower().startswith("transform first:"):
                problems.append(f"{where}: transform first must name the transform")
    return problems


def _check_identity(item: dict[str, Any], where: str, known: set[str],
                    methods: dict[str, dict[str, Any]], old_engine: bool,
                    problems: list[str]) -> None:
    solution = item.get("solution")
    if solution not in known:
        problems.append(f"{where}: cites {solution!r}, which is not in the catalogue")

    methodology = item.get("methodology")
    method = methods.get(methodology)
    if method is None:
        if old_engine and re.fullmatch(r"M-\d+", str(methodology)):
            if item.get("runnable") is not False:
                problems.append(f"{where}: only M-1 is runnable on the old-engine fallback")
            return
        problems.append(f"{where}: {methodology!r} is not in list_methodologies")
        return
    runnable = bool(method.get("runnable"))
    if item.get("runnable") is not runnable:
        problems.append(f"{where}: runnable must be {runnable} for {methodology}")
    solutions = method.get("solutions") or []
    if solutions and solution not in solutions:
        problems.append(f"{where}: {solution} is not a solution for {methodology}")


def _check_unsupported(item: dict[str, Any], where: str,
                       methods: dict[str, dict[str, Any]], problems: list[str]) -> None:
    methodology = item.get("methodology")
    method = methods.get(methodology) or {}
    reason = str(item.get("reason") or "")
    if method.get("runnable"):
        problems.append(
            f"{where}: {methodology} is runnable and must not be declined as unsupported"
        )
    if f"needs {methodology}" not in reason or "not supported yet" not in reason:
        problems.append(f"{where}: unsupported reason must name {methodology}")
    issue = method.get("issue")
    if issue and issue not in reason:
        problems.append(f"{where}: unsupported reason must name {issue}")
