"""The workspace profile Auto-Suggest reads for itself when a turn carries none (lake#269).

In lake, *Scan my workspace* hands the agent a read-only credential for the customer's workspace
(`zetta_base_url` is lake's `/workspaces/<customer>/zetta` path, so lake's allow-lists and audit
apply) instead of building the profile inside the person's request. The profile is what lake used
to build: every dataset's database, name, columns and rows, up to twelve, read with the node's
own `list_datasets` and `get_dataset`. Standard library only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

MAX_TABLES = 12

# (url, bearer) -> the JSON envelope {ok, value | error}
Get = Callable[[str, str], dict[str, Any]]


def _urllib_get(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read().decode())
        except json.JSONDecodeError:
            return {"ok": False, "error": {"message": f"HTTP {error.code}"}}


def read_profile(base_url: str, token: str, get: Get | None = None) -> dict[str, Any]:
    """The workspace's datasets as a profile. An unreadable dataset keeps its name, no columns."""
    get = get or _urllib_get
    base = base_url.rstrip("/")

    def read(capability: str, params: dict[str, str]) -> dict[str, Any] | None:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        answer = get(f"{base}/v1/{capability}{query}", token)
        return answer.get("value") if isinstance(answer, dict) and answer.get("ok") else None

    profile: dict[str, Any] = {"industry": "", "persona": "", "tables": []}
    listed = read("list_datasets", {}) or {}
    for dataset in (listed.get("datasets") or [])[:MAX_TABLES]:
        name = dataset if isinstance(dataset, str) else dataset.get("name", "")
        database = "datasets" if isinstance(dataset, str) else dataset.get("database", "datasets")
        table: dict[str, Any] = {"database": database, "name": name, "columns": []}
        got = read("get_dataset", {"dataset": name, "database": database})
        if got:
            table["columns"] = got.get("columns") or []
            if "rows" in got:
                table["rows"] = got["rows"]
        profile["tables"].append(table)
    return profile
