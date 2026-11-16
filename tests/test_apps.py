# SPDX-License-Identifier: MIT

"""Read-only Ship App and build evidence without a live lake."""

from __future__ import annotations

import json
import urllib.parse

from agent_autosuggest import apps, workspace


class FakeLake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def query(self, capability, inputs):
        self.calls.append((capability, inputs))
        if capability == "list_apps":
            return {
                "apps": [{"id": "app-1", "name": "revenue-dashboard"}],
                "app_limit": 10,
                "replica_limit": 20,
            }
        if capability == "get_app":
            return {
                "id": inputs["app_id"],
                "name": "revenue-dashboard",
                "phase": "Running",
                "ready": True,
            }
        if capability == "receive_app_events":
            return {"events": [{"phase": "Running", "sequence": 3}], "next_sequence": 4}
        if capability == "list_builds":
            return {"builds": [{"id": "build-1", "phase": "Running"}]}
        if capability == "get_build":
            return {"id": inputs["build_id"], "phase": "Running", "target": "revenue:v1"}
        if capability == "receive_build_events":
            return {"events": [{"type": "phase", "sequence": 8}], "next_sequence": 9}
        raise AssertionError(f"unexpected capability {capability}")


def test_inspection_reads_app_build_details_and_events_from_a_faked_lake():
    lake = FakeLake()

    evidence = apps.inspect(lake.query)

    assert evidence["source"] == "lake App and build capabilities"
    assert evidence["apps"][0]["app"]["phase"] == "Running"
    assert evidence["apps"][0]["events"][0]["sequence"] == 3
    assert evidence["builds"][0]["build"]["target"] == "revenue:v1"
    assert evidence["builds"][0]["events"][0]["sequence"] == 8
    assert lake.calls == [
        ("list_apps", {}),
        ("list_builds", {}),
        ("get_app", {"app_id": "app-1"}),
        ("receive_app_events", {"app_id": "app-1", "from_sequence": 1, "wait_ms": 0}),
        ("get_build", {"build_id": "build-1"}),
        ("receive_build_events", {"build_id": "build-1", "from_sequence": 1, "wait_ms": 0}),
    ]


def test_unknown_app_and_build_capabilities_use_a_compatible_fallback():
    calls = []

    def old_lake(capability, inputs):
        calls.append((capability, inputs))
        raise workspace.CapabilityError("unknown_capability", "not deployed")

    evidence = apps.inspect(old_lake)

    assert evidence["source"] == "lake App/build fallback"
    assert evidence["apps"] == []
    assert evidence["builds"] == []
    assert calls == [("list_apps", {}), ("list_builds", {})]


def test_lake_query_uses_get_with_customer_scope_and_the_turn_credential():
    sent = []

    def get(url, token):
        sent.append((url, token))
        return {"ok": True, "value": {"events": [], "next_sequence": 1}}

    query = workspace.make_lake_query("http://lake/", "lat.task.secret", "customer-1", get)
    answer = query(
        "receive_app_events",
        {"app_id": "app-1", "from_sequence": 1, "wait_ms": 0},
    )

    assert answer == {"events": [], "next_sequence": 1}
    parsed = urllib.parse.urlparse(sent[0][0])
    assert parsed.path == "/v1/receive_app_events"
    assert dict(urllib.parse.parse_qsl(parsed.query)) == {
        "customer_id": "customer-1",
        "app_id": "app-1",
        "from_sequence": json.dumps(1),
        "wait_ms": json.dumps(0),
    }
    assert sent[0][1] == "lat.task.secret"
