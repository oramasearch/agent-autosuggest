"""Current Ship App and image-build state from lake.

These are lake control-plane reads, not workspace-engine calls. Auto-Suggest takes a bounded,
non-blocking snapshot for each turn so existing Apps and in-flight builds can inform a proposal.
"""

from __future__ import annotations

import json
from typing import Any

from agent_autosuggest import methodologies, workspace

Query = methodologies.Query
MAX_RECORDS = 12

READ_CAPABILITIES = (
    "list_apps",
    "get_app",
    "receive_app_events",
    "list_builds",
    "get_build",
    "receive_build_events",
)

PROPOSABLE_ACTIONS = (
    "create_app",
    "deploy_app",
    "build_app",
    "rollback_app",
    "scale_app",
    "start_app",
    "stop_app",
    "restart_app",
    "delete_app",
    "set_app_env",
    "unset_app_env",
    "set_app_auth",
    "set_app_zetta_binding",
    "bind_service_to_app",
    "unbind_service_from_app",
    "create_service_and_bind_to_app",
    "cancel_build",
)


def inspect(query: Query | None) -> dict[str, Any]:
    """Read existing Apps, builds, and their retained events for this turn."""
    if query is None:
        return _fallback("lake read credentials are unavailable for this turn")

    app_list = _list(query, "list_apps", "apps")
    build_list = _list(query, "list_builds", "builds")
    if app_list is None and build_list is None:
        return _fallback("App and build read capabilities are unknown to this lake")

    evidence: dict[str, Any] = {
        "source": "lake App and build capabilities",
        "read_capabilities": list(READ_CAPABILITIES),
        "apps": _records(query, app_list, "app") if app_list is not None else [],
        "builds": _records(query, build_list, "build") if build_list is not None else [],
        "unavailable": [],
    }
    if app_list is None:
        evidence["unavailable"].append("list_apps")
    else:
        evidence["app_limit"] = app_list.get("app_limit")
        evidence["replica_limit"] = app_list.get("replica_limit")
    if build_list is None:
        evidence["unavailable"].append("list_builds")
    return evidence


def as_context(evidence: dict[str, Any]) -> str:
    """Render App state and the confirmation boundary as model context."""
    return (
        "\n\n## Current Ship Apps and image builds\n\n"
        "This JSON is a read-only snapshot from lake for this turn. The ApplicationAgent "
        "(`agent-application`, turn kind `app`) builds and changes Apps. Auto-Suggest may name "
        "actions from `proposable_actions`, but it must only propose them for a person to confirm; "
        "it never performs them.\n\n"
        "```json\n"
        + json.dumps(
            {**evidence, "proposable_actions": list(PROPOSABLE_ACTIONS)},
            indent=2,
            sort_keys=True,
        )
        + "\n```\n"
    )


def _list(query: Query, capability: str, key: str) -> dict[str, Any] | None:
    try:
        value = query(capability, {})
    except workspace.CapabilityError as error:
        if error.code == "unknown_capability":
            return None
        raise
    entries = value.get(key)
    if not isinstance(entries, list):
        raise workspace.CapabilityError(
            "invalid_response",
            f"{capability} did not return a {key} list",
        )
    return value


def _records(query: Query, listed: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    list_key = f"{kind}s"
    identifier_key = f"{kind}_id"
    get_capability = f"get_{kind}"
    events_capability = f"receive_{kind}_events"
    records: list[dict[str, Any]] = []
    for item in listed.get(list_key, [])[:MAX_RECORDS]:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            continue
        detail = _optional(query, get_capability, {identifier_key: identifier})
        event_page = _optional(
            query,
            events_capability,
            {identifier_key: identifier, "from_sequence": 1, "wait_ms": 0},
        )
        record: dict[str, Any] = {kind: detail if "unavailable" not in detail else item}
        if "unavailable" in detail:
            record[get_capability] = detail
        if "unavailable" in event_page:
            record[events_capability] = event_page
        else:
            record["events"] = event_page.get("events") or []
            record["next_sequence"] = event_page.get("next_sequence", 1)
        records.append(record)
    return records


def _optional(query: Query, capability: str, inputs: dict[str, Any]) -> dict[str, Any]:
    try:
        return query(capability, inputs)
    except workspace.CapabilityError as error:
        return {"unavailable": error.code, "message": str(error)}


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "source": "lake App/build fallback",
        "reason": reason,
        "read_capabilities": list(READ_CAPABILITIES),
        "apps": [],
        "builds": [],
        "unavailable": ["list_apps", "list_builds"],
    }
