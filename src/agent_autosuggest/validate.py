"""Check runtime solution or legacy methodology claims before they reach a caller."""

from __future__ import annotations

import re
from typing import Any

from agent_autosuggest import catalog, methodologies, solutions


def defects(
    answer: dict[str, Any],
    evidence: dict[str, Any] | None = None,
    solution_evidence: dict[str, Any] | None = None,
) -> list[str]:
    """Everything wrong with this project profile. Empty means it is usable."""
    solution_evidence = solution_evidence or solutions.inspect(None)
    if solutions.is_current(solution_evidence):
        return _solution_defects(answer, solution_evidence)

    return _methodology_defects(answer, evidence)


def _methodology_defects(
    answer: dict[str, Any],
    evidence: dict[str, Any] | None,
) -> list[str]:
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
    _check_product_claims(answer, problems)
    return problems


def _solution_defects(
    answer: dict[str, Any],
    evidence: dict[str, Any],
) -> list[str]:
    problems: list[str] = []
    registry = solutions.by_id(evidence)
    for index, item in enumerate(answer.get("suggested") or []):
        where = f"suggested[{index}]"
        solution = _check_solution_identity(item, where, registry, problems)
        if solution is not None and not solution.get("runnable"):
            problems.append(f"{where}: a suggested solution must be runnable")
        if item.get("runnable") is not True:
            problems.append(f"{where}: a suggested opportunity must be runnable")
        if not item.get("why_this_data_supports_it"):
            problems.append(f"{where}: no grounding in the profile's columns")

    for index, item in enumerate(answer.get("declined") or []):
        where = f"declined[{index}]"
        solution = _check_solution_identity(item, where, registry, problems)
        allowed = {"capture it first", "cannot be done", "not supported yet", "transform first"}
        if item.get("answer") not in allowed:
            problems.append(f"{where}: {item.get('answer')!r} is not a supported decline answer")
        if item.get("answer") == "not supported yet" and solution is not None:
            _check_solution_unsupported(item, where, solution, problems)
        if item.get("answer") == "transform first":
            reason = str(item.get("reason") or "")
            if not reason.lower().startswith("transform first:"):
                problems.append(f"{where}: transform first must name the transform")

    _check_product_claims(answer, problems)
    return problems


def _check_solution_identity(
    item: dict[str, Any],
    where: str,
    registry: dict[str, dict[str, Any]],
    problems: list[str],
) -> dict[str, Any] | None:
    identifier = item.get("solution")
    solution = registry.get(identifier)
    if solution is None:
        problems.append(f"{where}: {identifier!r} is not in list_solutions")
        return None

    level = item.get("level")
    levels = solutions.available_levels(solution)
    if solution.get("status") != "sketch" and level not in levels:
        problems.append(f"{where}: level {level!r} is not available for {identifier}")
    expected = solutions.selected_methodologies(solution, level)
    if item.get("methodologies") != expected:
        problems.append(
            f"{where}: methodologies must be {expected!r} for {identifier} level {level!r}"
        )
    runnable = bool(solution.get("runnable"))
    if item.get("runnable") is not runnable:
        problems.append(f"{where}: runnable must be {runnable} for {identifier}")
    return solution


def _check_solution_unsupported(
    item: dict[str, Any],
    where: str,
    solution: dict[str, Any],
    problems: list[str],
) -> None:
    reason = str(item.get("reason") or "")
    if solution.get("runnable"):
        problems.append(f"{where}: a runnable solution must not be declined as unsupported")
        return
    if solution.get("status") == "sketch":
        if "sketch" not in reason.lower():
            problems.append(f"{where}: a sketch refusal must say it is a sketch")
        promotion = solution.get("promotion_condition")
        if promotion and promotion not in reason:
            problems.append(f"{where}: a sketch refusal must name its promotion condition")
        issue = solution.get("issue")
        if issue and issue not in reason:
            problems.append(f"{where}: a sketch refusal must name {issue}")
        return

    missing = solutions.missing_components(solution)
    if missing and not reason.startswith("the pipeline builds; production operation needs "):
        problems.append(f"{where}: missing components must distinguish build from operation")
    for component in missing:
        for fact in (component.get("name") or component.get("id"), component.get("issue")):
            if fact and str(fact) not in reason:
                problems.append(f"{where}: unsupported reason must name {fact}")


def _check_product_claims(answer: dict[str, Any], problems: list[str]) -> None:
    text = str(answer)
    forbidden = [
        (
            r"\b\d+(?:\.\d+)?%\s+(?:roi|return|chance|odds|success)\b",
            "numerical return or success claim",
        ),
        (
            r"\b(?:roi|return|chance|odds|success(?:\s+chance)?)\s+"
            r"(?:of\s+)?\d+(?:\.\d+)?%",
            "numerical return or success claim",
        ),
        (
            r"(?:\$|usd\s*)\d+(?:[,.]\d+)*\s*(?:per\s+month|monthly|per\s+year|yearly)",
            "pricing claim",
        ),
    ]
    for pattern, label in forbidden:
        if re.search(pattern, text, re.IGNORECASE):
            problems.append(f"answer contains a {label}")


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
