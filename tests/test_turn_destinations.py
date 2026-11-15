"""A turn's credential goes only to Lake at LAKE_URL, never to an address the request names (#21)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_autosuggest import service, workspace
from agent_autosuggest.retrieval import Corpus

LAKE = "http://lake-prod.lake.svc.cluster.local:80"
ROUTE = LAKE + "/workspaces/cust-1/zetta"


@pytest.fixture
def sent(monkeypatch):
    """Every request the service makes through lake (GET or POST) and every model client built."""
    requests: list[tuple[str, str]] = []

    def get(url, token):
        requests.append((url, token))
        return {"ok": True, "value": {"datasets": []}}

    def post(url, token, body):
        requests.append((url, token))
        return {"ok": True, "value": {}}

    monkeypatch.setattr(workspace, "_urllib_get", get)
    monkeypatch.setattr(workspace, "_urllib_post", post)
    monkeypatch.setattr(
        service, "gateway_client", lambda url, token, **kw: pytest.fail(f"model called at {url}")
    )
    return requests


def configured(tmp_path: Path, lake: str) -> service.Settings:
    value = service.Settings()
    value.trace_dir = tmp_path / "traces"
    value.corpus_path = tmp_path / "missing.jsonl"
    value.lake_url = lake
    return value


def turn(tmp_path, lake, body):
    return service.run_turn(body, configured(tmp_path, lake), Corpus(tmp_path / "missing.jsonl"))


@pytest.mark.parametrize(
    "zetta_base_url",
    [
        "http://attacker.example/workspaces/cust-1/zetta",
        "https://lake-prod.lake.svc.cluster.local/workspaces/cust-1/zetta",
        "http://lake-prod.lake.svc.cluster.local:8080/workspaces/cust-1/zetta",
        "http://lake-prod.lake.svc.cluster.local.attacker.example/workspaces/cust-1/zetta",
        "http://user@lake-prod.lake.svc.cluster.local/workspaces/cust-1/zetta",
        "http://lake-prod.lake.svc.cluster.local:80@attacker.example/zetta",
        "http://lake-prod.lake.svc.cluster.local/workspaces/c/zetta?next=http://attacker.example",
        "file:///etc/passwd",
        "lake-prod.lake.svc.cluster.local/workspaces/cust-1/zetta",
        "http://lake-prod.lake.svc.cluster.local:notaport/zetta",
    ],
)
def test_a_workspace_address_off_lakes_origin_is_refused_before_any_request(
    tmp_path, sent, zetta_base_url
):
    answer = turn(tmp_path, LAKE, {"request": "x", "zetta_base_url": zetta_base_url,
                                   "zetta_token": "lat.task.secret",
                                   "lake_token": "lat.task.secret", "customer_id": "cust-1"})
    assert not answer["ok"]
    assert "zetta_base_url is not Lake's workspace route" in answer["error"]
    assert sent == []


def test_lakes_workspace_route_is_read_with_the_turns_credential(tmp_path, sent):
    answer = turn(tmp_path, LAKE, {"request": "x", "zetta_base_url": ROUTE,
                                   "zetta_token": "lat.task.secret"})
    # The empty workspace ends the turn before the model, after reading through Lake.
    assert not answer["ok"] and "load data" in answer["error"]
    assert sent and all(url.startswith(ROUTE + "/v1/") for url, _ in sent)
    assert {token for _, token in sent} == {"lat.task.secret"}


def test_the_default_port_is_the_same_origin(tmp_path, sent):
    route = "http://LAKE-PROD.lake.svc.cluster.local/workspaces/cust-1/zetta"
    answer = turn(tmp_path, LAKE, {"request": "x", "zetta_base_url": route, "zetta_token": "t"})
    assert "load data" in answer["error"]
    assert sent


def test_lakes_capabilities_are_read_at_lake_url_never_the_turns_lake_url(tmp_path, sent):
    profile = {"tables": [{"name": "accounts", "columns": ["id"]}]}
    answer = turn(tmp_path, LAKE, {"profile": profile, "lake_url": "http://attacker.example",
                                   "lake_token": "lat.task.secret", "customer_id": "cust-1"})
    # The fixture's empty answers end the turn with a capability error before the model.
    assert sent, "lake was never read"
    assert all(url.startswith(LAKE + "/v1/") for url, _ in sent), sent
    assert not answer["ok"]


def test_without_lake_url_no_turn_sends_its_credential(tmp_path, sent):
    for lake in ("", "lake-prod.lake.svc.cluster.local", "ftp://lake"):
        answer = turn(tmp_path, lake, {"request": "x", "zetta_base_url": ROUTE, "zetta_token": "t",
                                       "lake_token": "t", "lake_url": LAKE, "customer_id": "c"})
        assert not answer["ok"] and "LAKE_URL is not set" in answer["error"]
    assert sent == []


def test_a_workspace_route_must_be_under_lake_urls_path():
    lake = "http://lake:80/base"
    assert service.workspace_url("http://lake/base/w/1/zetta", lake) == "http://lake/base/w/1/zetta"
    assert service.workspace_url("http://lake/basement/w/1/zetta", lake) is None
    assert service.workspace_url("http://lake/other/w/1/zetta", lake) is None
    assert service.workspace_url("http://lake/base/../admin", lake) is None
