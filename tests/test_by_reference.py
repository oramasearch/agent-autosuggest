"""Task credentials by reference (lake#871, lake docs/api/agent-bus.md).

A claim asks Lake for `credentials: by_reference`: the body arrives with no credential, and every
call made for the task carries this agent's bus credential and `X-Lake-Agent-Task: <task id>`. A
Lake from before lake#871 refuses the field; the claim is asked again and the body's own credential
is used, as before.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_autosuggest import lake_bus, model, service, workspace
from agent_autosuggest.retrieval import Corpus
from test_service import PROFILE, FakeEngine, FakeLakeState

LAKE = "http://lake-prod.lake.svc.cluster.local:80"
ROUTE = LAKE + "/workspaces/c/zetta"
TASK = {"task_id": "t1", "customer_id": "c", "workspace_id": "w", "kind": "suggest"}
HEADER = {"X-Lake-Agent-Task": "t1"}


class BusLake:
    """lake's bus in memory. `supports_by_reference` False is a Lake from before lake#871."""

    def __init__(self, supports_by_reference: bool) -> None:
        self.supports_by_reference = supports_by_reference
        self.claims: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.stop = threading.Event()
        self.received = False

    def send(self, method: str, url: str, headers: dict[str, str],
             body: bytes | None) -> tuple[int, str]:
        self.headers.append(headers)
        capability = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
        if capability == "receive_agent_tasks":
            tasks = [] if self.received else [{"sequence": 1, **TASK}]
            self.received = True
            if not tasks:
                self.stop.set()
            return 200, json.dumps({"ok": True, "value": {"tasks": tasks, "next_sequence": 2}})
        arguments = json.loads(body or b"{}")
        if capability == "claim_agent_task":
            self.claims.append(arguments)
            if "credentials" in arguments and not self.supports_by_reference:
                return 400, json.dumps({"ok": False, "error": {
                    "code": "invalid_input", "message": "unexpected field credentials",
                    "path": "/credentials"}})
            by_reference = arguments.get("credentials") == "by_reference"
            credential = "" if by_reference else "lat.t1.secret"
            value = {**TASK, "body": {"request": "hi", "lake_token": credential,
                                      "zetta_base_url": ROUTE, "zetta_token": credential}}
            if by_reference:
                value["credentials"] = "by_reference"
            return 200, json.dumps({"ok": True, "value": value})
        return 200, json.dumps({"ok": True, "value": {}})


def handed_body(lake: BusLake) -> dict[str, Any]:
    handed: list[dict[str, Any]] = []
    bus = lake_bus.LakeBus("http://lake", "bus-token", "agent-autosuggest", send=lake.send)
    lake_bus.work(bus, lambda body: handed.append(body) or {"ok": True}, lake.stop, workers=1)
    assert len(handed) == 1, handed
    return handed[0]


def test_a_claim_asks_by_reference_and_the_turn_holds_no_task_credential():
    lake = BusLake(supports_by_reference=True)
    body = handed_body(lake)
    assert lake.claims == [{"task_id": "t1", "agent": "agent-autosuggest",
                            "credentials": "by_reference"}]
    assert body["lake_token"] == "bus-token", "the turn calls Lake with the bus credential"
    assert body["zetta_token"] == "bus-token"
    assert body["lake_headers"] == HEADER
    assert lake_bus.task_headers(body) == HEADER
    # Bus calls themselves never name a task: Lake would answer them as the task.
    assert all(lake_bus.TASK_HEADER not in headers for headers in lake.headers)


def test_a_lake_from_before_by_reference_is_claimed_again_with_the_credential_in_the_body():
    lake = BusLake(supports_by_reference=False)
    body = handed_body(lake)
    assert lake.claims == [
        {"task_id": "t1", "agent": "agent-autosuggest", "credentials": "by_reference"},
        {"task_id": "t1", "agent": "agent-autosuggest"},
    ]
    assert body["lake_token"] == "lat.t1.secret", "the body's own credential is used, as before"
    assert body["zetta_token"] == "lat.t1.secret"
    assert "lake_headers" not in body
    assert lake_bus.task_headers(body) == {}


def test_a_claim_refused_for_another_reason_is_not_asked_again():
    assert not lake_bus.refuses_by_reference(
        lake_bus.BusError("invalid_input", "task_id is required"))
    assert lake_bus.refuses_by_reference(
        lake_bus.BusError("invalid_input", "unexpected field credentials"))


def test_task_headers_take_only_the_task_header():
    assert lake_bus.task_headers({"lake_headers": {"X-Lake-Agent-Task": "t1",
                                                   "Authorization": "Bearer other"}}) == HEADER
    assert lake_bus.task_headers({"lake_headers": "t1"}) == {}
    assert lake_bus.task_headers({}) == {}


@pytest.fixture
def sent(monkeypatch):
    """Every request made through Lake's workspace route and capabilities, with its headers."""
    requests: list[tuple[str, str, dict[str, str]]] = []

    def get(url, token, headers=None):
        requests.append((url, token, dict(headers or {})))
        if url.startswith(ROUTE + "/v1/list_datasets"):
            return {"ok": True, "value": {"datasets": ["accounts"]}}
        if url.startswith(ROUTE + "/v1/get_dataset"):
            return {"ok": True, "value": {"columns": ["id"]}}
        return {"ok": True, "value": {}}

    def post(url, token, body, headers=None):
        requests.append((url, token, dict(headers or {})))
        return {"ok": True, "value": {}}

    monkeypatch.setattr(workspace, "_urllib_get", get)
    monkeypatch.setattr(workspace, "_urllib_post", post)
    monkeypatch.setattr(
        service, "gateway_client", lambda url, token, **kw: pytest.fail(f"model called at {url}"))
    return requests


def configured(tmp_path: Path) -> service.Settings:
    value = service.Settings()
    value.trace_dir = tmp_path / "traces"
    value.corpus_path = tmp_path / "missing.jsonl"
    value.lake_url = LAKE
    return value


def by_reference_turn(**body: Any) -> dict[str, Any]:
    claimed = {**TASK, "credentials": "by_reference",
               "body": {"lake_token": "", "zetta_token": "", "zetta_base_url": ROUTE, **body}}
    return {**lake_bus.task_credentials("bus-token", claimed), "customer_id": "c"}


def test_every_workspace_and_lake_read_of_a_by_reference_turn_names_its_task(tmp_path, sent):
    # The fixture's empty capability answers end the turn before the model.
    service.run_turn(by_reference_turn(request="x"), configured(tmp_path),
                     Corpus(tmp_path / "missing.jsonl"))
    assert any(url.startswith(ROUTE + "/v1/") for url, _, _ in sent), "the scan never ran"
    assert any(url.startswith(LAKE + "/v1/") and not url.startswith(ROUTE) for url, _, _ in sent)
    for url, token, headers in sent:
        assert token == "bus-token", url
        assert headers == HEADER, url


def test_a_by_reference_turns_model_calls_name_its_task(tmp_path, monkeypatch):
    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "gpt-4o",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(service, "gateway_client", lambda url, token, **kw: model.gateway_client(
        url, token, http_client=httpx.Client(transport=httpx.MockTransport(gateway)), **kw))
    turn = by_reference_turn(context={"profile": PROFILE})
    del turn["zetta_base_url"]
    service.run_turn(turn, configured(tmp_path), Corpus(tmp_path / "missing.jsonl"), None,
                     query=FakeEngine([]).query, lake_query=FakeLakeState().query)
    assert seen, "the model was never called"
    for request in seen:
        assert request.headers["authorization"] == "Bearer bus-token"
        assert request.headers["x-lake-agent-task"] == "t1"


def test_a_turn_with_its_credential_in_the_body_sends_no_task_header(tmp_path, sent):
    claimed = {**TASK, "body": {"request": "x", "lake_token": "lat.t1.secret",
                                "zetta_token": "lat.t1.secret", "zetta_base_url": ROUTE}}
    turn = {**lake_bus.task_credentials("bus-token", claimed), "customer_id": "c"}
    service.run_turn(turn, configured(tmp_path), Corpus(tmp_path / "missing.jsonl"))
    assert sent
    for url, token, headers in sent:
        assert token == "lat.t1.secret", url
        assert headers == {}, url
