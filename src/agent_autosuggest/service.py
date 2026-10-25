"""Service mode: Auto-Suggest as a long-running agent lake calls (lake#228, agent-autosuggest#1).

In lake, Design's *Discover Opportunities* shows its `suggested` entries as ranked Opportunities;
Ask Orama calls it as a subagent for "what could I build?". One turn is one `suggest`. The agent
reads the workspace profile, current Ship Apps, and builds with the task's read-only credential.

Standard library only, like the Project Agent's service.
"""

from __future__ import annotations

import hmac
import json
import os
import uuid
from importlib.metadata import PackageNotFoundError, version
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from openai import OpenAI

from agent_autosuggest import apps, lake_bus, methodologies, solutions, validate, workspace
from agent_autosuggest.agent import PROMPT_VERSION, suggest
from agent_autosuggest.model import gateway_client
from agent_autosuggest.retrieval import Corpus


class Settings:
    """What the service reads from its environment, once."""

    def __init__(self) -> None:
        self.corpus_path = Path(
            os.environ.get("AUTOSUGGEST_CORPUS", "/app/corpus/autosuggest-sft-v3.jsonl"))
        self.model = os.environ.get("AUTOSUGGEST_MODEL", "gpt-4o")
        self.lake_url = os.environ.get("LAKE_URL", "")
        self.token = os.environ.get("AGENT_SERVICE_TOKEN", "")
        self.shots = int(os.environ.get("AUTOSUGGEST_SHOTS", "3"))


DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int] | None:
    """`(scheme, host, port)` of an absolute http(s) URL with no user info, or None."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or not parts.hostname or "@" in parts.netloc:
        return None
    return scheme, parts.hostname.lower(), port or DEFAULT_PORTS[scheme]


def lakes_url(settings: Settings) -> str:
    """Lake's address from this service's environment (`LAKE_URL`), the only place a turn's
    credential is ever sent; empty when unset or not an absolute http(s) URL."""
    lake = settings.lake_url.strip().rstrip("/")
    return lake if lake and _origin(lake) is not None else ""


def workspace_url(requested: str, lake: str) -> str | None:
    """The turn's `zetta_base_url` when it is Lake's workspace route, else None.

    Lake hands the agent its read path to the person's workspace
    (`<LAKE_URL>/workspaces/<customer>/zetta`), and the turn's credential is sent there. The
    address comes from the request, so it is accepted only when it is on Lake's own origin
    (scheme, host and port of `LAKE_URL`) under `LAKE_URL`'s path, with no user info, query or
    fragment; anything else would send the bearer to a host the caller chose.
    """
    want = _origin(lake)
    got = _origin(requested)
    if want is None or got is None or got != want:
        return None
    parts, base = urlsplit(requested), urlsplit(lake)
    if parts.query or parts.fragment or "\\" in requested:
        return None
    prefix = base.path.rstrip("/")
    path = parts.path
    if prefix and not (path == prefix or path.startswith(prefix + "/")):
        return None
    if any(segment in (".", "..") for segment in path.split("/")):
        return None
    return requested.rstrip("/")


def profile_from_turn(turn: dict[str, Any]) -> dict[str, Any] | None:
    """The profile rides on the turn directly or in its context; a request alone is not a
    profile, and suggesting without one would be guessing at data the agent never saw."""
    profile = turn.get("profile")
    if not isinstance(profile, dict):
        profile = (turn.get("context") or {}).get("profile")
    if not isinstance(profile, dict) or not (profile.get("tables") or profile.get("connections")):
        return None
    if turn.get("request") and not profile.get("request"):
        profile = {**profile, "request": turn["request"]}
    return profile


def run_turn(turn: dict[str, Any], settings: Settings, corpus: Corpus,
             client: OpenAI | None = None, get: workspace.Get | None = None,
             query: methodologies.Query | None = None,
             lake_query: apps.Query | None = None) -> dict[str, Any]:
    profile = profile_from_turn(turn)
    scan = profile is None and bool(turn.get("zetta_base_url") and turn.get("zetta_token"))
    reads_lake = (query is None or lake_query is None) and bool(
        turn.get("lake_token") and turn.get("customer_id"))
    lake = lakes_url(settings)
    if not lake and (scan or reads_lake or client is None):
        # The turn's credential goes only to Lake at this service's own LAKE_URL, never to an
        # address the request names (agent-autosuggest#21).
        return {"ok": False, "error": "LAKE_URL is not set: this agent sends a turn's "
                                      "credential only to Lake's configured address"}
    if scan:
        # Scan my workspace (lake#269): read the workspace with the turn's read-only credential.
        base = workspace_url(str(turn["zetta_base_url"]), lake)
        if base is None:
            return {"ok": False, "error": "zetta_base_url is not Lake's workspace route at "
                                          "LAKE_URL; the turn's credential is never sent elsewhere"}
        profile = profile_from_turn({**turn, "profile": workspace.read_profile(
            base, turn["zetta_token"], get)})
        if profile is None:
            return {"ok": False, "error": "the workspace has no datasets yet: load data first"}
    if profile is None:
        return {"ok": False, "error": "a turn needs a profile with its tables or connections"}

    if reads_lake:
        # Always this service's LAKE_URL; a turn's own `lake_url` is never used.
        lake_token, customer_id = turn["lake_token"], turn["customer_id"]
        if query is None:
            query = workspace.make_query(lake, lake_token, customer_id)
        if lake_query is None:
            lake_query = workspace.make_lake_query(lake, lake_token, customer_id)
    try:
        methodology_evidence = methodologies.inspect(profile, query)
        solution_evidence = solutions.inspect(query)
        app_evidence = apps.inspect(lake_query)
    except workspace.CapabilityError as error:
        return {"ok": False, "error": f"{error.code}: {error}"}
    if client is None:
        # The model is reached through lake with the turn's own key, which counts its tokens in
        # the turn's workspace (lake#248); this agent holds no provider key. The address is this
        # service's LAKE_URL; the turn's own `lake_url` is never used, so the key cannot be sent
        # to a host the caller names (agent-autosuggest#21).
        model_key = turn.get("lake_token") or turn.get("zetta_token")
        if not model_key:
            return {"ok": False, "error": "a turn needs its lake_token to reach the model "
                                          "through lake"}
        client = gateway_client(lake, model_key)
    answer, used = suggest(
        profile,
        corpus,
        client,
        turn.get("model") or settings.model,
        settings.shots,
        methodology_evidence,
        solution_evidence,
        query,
        app_evidence,
    )
    if answer is None:
        return {"ok": False, "error": "no parseable project profile came back"}
    result = {
        "project_profile": answer,
        "few_shot_from": used,
        "prompt_version": PROMPT_VERSION,
        "defects": validate.defects(
            answer,
            methodology_evidence,
            solution_evidence,
            app_evidence,
        ),
    }
    # The trace id names the turn; lake stores it with the answer in the turn's workspace Zetta.
    # Nothing is written to the pod's disk: the pod serves every tenant, and the profile, the
    # evidence and the answer are workspace content (agent-autosuggest#13).
    trace_id = str(uuid.uuid4())
    return {"ok": True, "trace_id": trace_id, "result": result}


def _version() -> str:
    try:
        return version("agent-autosuggest")
    except PackageNotFoundError:
        return "unknown"


def health(settings: Settings, corpus: Corpus) -> tuple[int, dict[str, Any]]:
    """Unhealthy without the corpus: answering zero-shot is a different agent."""
    if not corpus.examples:
        return 503, {"ok": False, "error": f"no corpus at {settings.corpus_path}"}
    return 200, {"ok": True, "agent": "agent-autosuggest", "examples": len(corpus.examples),
                 "model": settings.model, "version": _version()}


def turn_refusal(authorization: str | None, token: str) -> tuple[int, str] | None:
    """Why `POST /v1/turn` is refused, as `(status, error)`, or None when the caller may proceed.

    The route fails closed: with `AGENT_SERVICE_TOKEN` unset or empty every turn is refused, so a
    service started without it is never open. The bearer is compared in constant time
    (`hmac.compare_digest` over bytes), so the comparison's timing says nothing about the token.
    """
    if not token:
        return 503, "AGENT_SERVICE_TOKEN is not set; this service refuses every turn"
    expected = f"Bearer {token}".encode()
    supplied = (authorization or "").encode("utf-8", "surrogateescape")
    if not hmac.compare_digest(supplied, expected):
        return 401, "unauthorized"
    return None


def make_handler(settings: Settings, corpus: Corpus) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 (the stdlib's name)
            if self.path == "/healthz":
                self._send(*health(settings, corpus))
                return
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/turn":
                self._send(404, {"ok": False, "error": "not found"})
                return
            refusal = turn_refusal(self.headers.get("Authorization"), settings.token)
            if refusal is not None:
                status, error = refusal
                self._send(status, {"ok": False, "error": error})
                return
            try:
                turn = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            except (json.JSONDecodeError, ValueError):
                self._send(400, {"ok": False, "error": "the body is not JSON"})
                return
            try:
                answer = run_turn(turn if isinstance(turn, dict) else {}, settings, corpus)
            except Exception as error:  # a failed suggestion is an answer, not a dead worker
                answer = {"ok": False, "error": f"{type(error).__name__}: {error}"}
            self._send(200 if answer.get("ok") else 422, answer)

        def log_message(self, format: str, *args: Any) -> None:
            print(json.dumps({"agent": "agent-autosuggest", "request": format % args}), flush=True)

    return Handler


def serve() -> int:
    settings = Settings()
    corpus = Corpus(settings.corpus_path)
    port = int(os.environ.get("PORT", "8080"))
    print(json.dumps({"agent": "agent-autosuggest", "listening": port,
                      "examples": len(corpus.examples)}), flush=True)
    # Turns arrive as tasks on lake's agent bus (lake#269); /v1/turn stays for direct calls.
    lake_bus.start_from_env(lambda turn: run_turn(turn, settings, corpus), "agent-autosuggest")
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(settings, corpus)).serve_forever()
    return 0
