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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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
        self.trace_dir = Path(os.environ.get("TRACE_DIR", "/tmp/agent-autosuggest-traces"))
        self.shots = int(os.environ.get("AUTOSUGGEST_SHOTS", "3"))


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
    if profile is None and turn.get("zetta_base_url") and turn.get("zetta_token"):
        # Scan my workspace (lake#269): read the workspace with the turn's read-only credential.
        profile = profile_from_turn({**turn, "profile": workspace.read_profile(
            turn["zetta_base_url"], turn["zetta_token"], get)})
        if profile is None:
            return {"ok": False, "error": "the workspace has no datasets yet: load data first"}
    if profile is None:
        return {"ok": False, "error": "a turn needs a profile with its tables or connections"}

    if query is None or lake_query is None:
        lake_url = turn.get("lake_url") or settings.lake_url
        lake_token = turn.get("lake_token")
        customer_id = turn.get("customer_id")
        if lake_url and lake_token and customer_id:
            if query is None:
                query = workspace.make_query(lake_url, lake_token, customer_id)
            if lake_query is None:
                lake_query = workspace.make_lake_query(lake_url, lake_token, customer_id)
    try:
        methodology_evidence = methodologies.inspect(profile, query)
        solution_evidence = solutions.inspect(query)
        app_evidence = apps.inspect(lake_query)
    except workspace.CapabilityError as error:
        return {"ok": False, "error": f"{error.code}: {error}"}
    if client is None:
        # The model is reached through lake with the turn's own key, which counts its tokens in
        # the turn's workspace (lake#248); this agent holds no provider key.
        model_url = turn.get("lake_url") or settings.lake_url
        model_key = turn.get("lake_token") or turn.get("zetta_token")
        if not model_url or not model_key:
            return {"ok": False, "error": "a turn needs lake_url and its lake_token to reach the "
                                          "model through lake"}
        client = gateway_client(model_url, model_key)
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
    trace_id = str(uuid.uuid4())
    settings.trace_dir.mkdir(parents=True, exist_ok=True)
    (settings.trace_dir / f"{trace_id}.json").write_text(json.dumps(
        {"trace_id": trace_id, "agent": "agent-autosuggest",
         "at": datetime.now(timezone.utc).isoformat(), "profile": profile,
         "methodology_evidence": methodology_evidence,
         "solution_evidence": solution_evidence,
         "app_evidence": app_evidence,
         "result": result},
        indent=1, default=str))
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
