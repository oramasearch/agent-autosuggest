# agent-autosuggest

Current release: [v0.2.0](docs/releases/v0.2.0.md) (every release: [`docs/releases/`](docs/releases/README.md)).

Reads a **profile** — a business situation and its data — and proposes what is worth building.

The output is a **project profile**: the same object `agent-project` consumes, so the two compose
without a translation layer that can drift.

## Not a trained model yet

A frontier model, the solutions catalogue as context, and few-shot examples retrieved from
`agent-autosuggest-corpus`. That corpus becomes SFT data later; until then it is the precedent this
agent reasons from — which also means it is continuously exercised rather than sitting unread until
a training run. See [`zetta-bridge/docs/agent-scaffold.md`](../../docs/agent-scaffold.md).

## Run

```bash
export OPENAI_API_KEY=...
export AUTOSUGGEST_CORPUS=../agent-autosuggest-corpus/corpus/autosuggest-sft-v3.jsonl
uv run agent-autosuggest path/to/profile.json
```

Exit `0` clean, `2` when the answer has defects (it still prints — you should see what was wrong
with it), `1` when nothing parseable came back.

## Service mode (in lake)

In lake Auto-Suggest is a specialized agent of the **Agent Library**: Design's *Discover
Opportunities* sends it the workspace's profile and shows its `suggested` entries as ranked
Opportunities, and Ask Orama calls it as a subagent ([lake#228](https://github.com/oramasearch/lake/issues/228),
[lake#237](https://github.com/oramasearch/lake/issues/237)). `agent-autosuggest-serve` runs it on
`PORT` (8080):

| Route | What it does |
|---|---|
| `GET /healthz` | `200` with the corpus size, model and package version, or `503` when the corpus is missing |
| `POST /v1/turn` | One suggestion. Body: `{"profile": {...}}` or `{"context": {"profile": {...}}}`, the profile carrying its tables or connections, optionally `request`. Answers `{ok, trace_id, result: {project_profile, few_shot_from, prompt_version, defects}}`; `422` with `error` otherwise |

**A turn's credential goes only to Lake at this service's own `LAKE_URL`** (#21). The model gateway
and lake's capabilities (`workspace_query`, `list_apps`, ...) are always reached at `LAKE_URL`; a
turn's `lake_url` is ignored. The workspace address, `zetta_base_url`, is Lake's workspace route
(`<LAKE_URL>/workspaces/<customer>/zetta`) and is accepted only on `LAKE_URL`'s origin (scheme, host
and port) under its path, with no user info, query or fragment; any other address refuses the turn
before a request is made. With `LAKE_URL` unset, every turn that would send a credential is refused.

**Scan my workspace** (lake#269) hands it no profile. Instead it gets `zetta_base_url` (lake's
`/workspaces/<customer>/zetta` path) and a read-only task credential as `zetta_token`, and
`workspace.py` reads the profile itself: every dataset's database, name, columns and rows, up to
twelve, via `list_datasets` and `get_dataset`, the shape lake used to build. A workspace with no
datasets is told to load data first. The credential never reaches the answer or the model's messages.

**Turns arrive on lake's agent bus** (lake `docs/api/agent-bus.md`). `lake_bus.py` long-polls
`receive_agent_tasks` through lake with this agent's own bus credential (`AGENT_BUS_TOKEN`). It
claims each task, runs it as `/v1/turn` would, and publishes `progress`, then the answer or
`failed`. It starts when the operator sets `LAKE_URL`, `AGENT_BUS_TOKEN` and `AGENT_BUS_AGENT`.
`AGENT_SERVICE_TOKEN` is required as a bearer, compared in constant time (`401` otherwise), and
with it unset or empty the route fails closed (`503` for every turn); nothing about a turn is written to the pod's disk;
`AUTOSUGGEST_MODEL` picks the model. **The model is reached through lake** (lake#248, lake
`docs/api/model-gateway.md`): every call goes to `<LAKE_URL>/openai/v1` with the turn's own
`lake_token` as the API key (`model.py`), so lake counts its tokens in the turn's workspace and
enforces the account's model-token limit. The service holds no `OPENAI_API_KEY`; a turn without a
lake credential is refused. Only the command line above, which runs for no workspace, calls OpenAI
directly with your own key.

A task is claimed only when a worker is free to run it at once (agent-autosuggest#23, LSR-075): while every
worker is busy nothing is received, and a task this replica cannot run stays unclaimed on the bus.
`AGENT_BUS_WORKERS` (default 4) run at once; one customer holds at most `AGENT_BUS_TENANT_WORKERS`
(default one fewer), and a freed worker goes to the waiting customer holding the fewest. At most 256
dispatch rows wait, never a claimed body. `tests/test_bus_backpressure.py` covers it.

**A turn keeps nothing on the pod** (agent-autosuggest#13). The pod serves every workspace, and a
turn's profile (rows from up to twelve datasets), evidence and answer are that workspace's content,
which belongs in its workspace Zetta. So no trace file is written: the answer goes back on the bus
(or the `/v1/turn` response), and lake stores it, with its `trace_id`, in the turn's workspace.
`TRACE_DIR` is no longer read.

**Solutions and methodologies are read from the engine for every turn.** Auto-Suggest calls lake's
`workspace_query` for `list_solutions` and `list_methodologies`. A current-engine Opportunity names
its S-n solution, selected level, composed methodology ids, and registry-derived `runnable` status.
Entries with missing production components say that the pipeline builds but operation still needs
the named components; Band-B sketches name their promotion condition and remain declined. It uses
`analyze_dataset` and `plan_features`: a composition every methodology plans `ready` on one dataset
is suggested, with any preparation as a step of the project, and data that must be transformed first
is declined naming the Lake capability that performs it (lake#1153). If
`list_solutions` returns `unknown_capability`, the agent preserves the methodology-only behavior;
if that registry is also unavailable, only M-1 is treated as runnable. See
[`docs/solutions/runtime-solutions.md`](docs/solutions/runtime-solutions.md) and
[`docs/methodologies/runtime-methodologies.md`](docs/methodologies/runtime-methodologies.md).

**Data can make an App the right Opportunity.** Auto-Suggest reads `list_apps`, `get_app`,
`receive_app_events`, `list_builds`, `get_build`, and `receive_build_events` directly from lake.
An App Opportunity names what its dashboard or internal tool shows and proposes the relevant App
actions for a person to confirm. It delegates the confirmed build or change to ApplicationAgent
(`agent-application`, turn kind `app`); Auto-Suggest never performs an App action. See
[`docs/apps/runtime-apps.md`](docs/apps/runtime-apps.md).

**The solutions catalogue** in the image is built at image-build time by zetta's own
`scripts/build_catalog.py` from the solutions-designs plan, and passed as the named build context
`catalog` (`catalog.json` and `catalog.source.json`, which records the zetta and solutions-designs
commits). `ZETTA_CATALOG` points at it; without it the agent builds the catalogue from a bridge
checkout, as before.

**Image:** `Dockerfile`, published **private** as `oramasearch/agent-autosuggest`, with the
corpus as the named build context `corpus` (`agent-autosuggest-corpus/corpus`), built on the
cluster BuildKit (`--local corpus=../agent-autosuggest-corpus/corpus --opt
context:corpus=local:corpus`). Tests: `uv run pytest`.

The Dockerfile pins its bases by multi-platform index digest (lake#1003): `python:3.12-slim@sha256:5e2bdb1b…` for both stages and `ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85a…`. Moving one is a reviewed change of its digest.

## The six parts

| file | job |
|---|---|
| `retrieval.py` | TF-IDF nearest situations. Similarity is over the **situation**, never the answer — retrieving by answer shows the model what it is supposed to conclude. |
| `catalog.py` | The solutions catalogue as **context, not a tool**. Built from the plan markdown by `zetta/scripts/build_catalog.py`, never vendored. |
| `methodologies.py` | Reads the workspace registry and feature evidence, then enforces runnable and transform-first decisions. |
| `solutions.py` | Reads the workspace's solution registry and enforces levels, composition, and runtime status. |
| `apps.py` | Reads current Ship Apps and builds, then enforces confirmation-gated ApplicationAgent proposals. |
| `validate.py` | Rejects claims that contradict the current solution or legacy methodology registry. |

## Why the catalogue is context

When the eval generator reached the catalogue through a tool, three independent runs produced the
same domain — whatever the tool returned first anchored everything after it. Held as context, the
whole catalogue is in view at once.

It is also the vocabulary. Without it the model invents plausible identifiers: an early run cited
`S-23` against a catalogue whose highest id is `S-12`. A citation nobody can look up is worse than
no citation, because it reads as grounded.

## Why a missing catalogue is a hard failure

`catalog.Unavailable` is raised, not swallowed. An empty catalogue makes the id check vacuously
pass — the agent then invents ids and the validator reports a clean answer. That is exactly how
`S-15` survived a validator that returned no defects.

Point `ZETTA_BRIDGE` at the bridge checkout if the parent walk cannot find it. An explicit
`ZETTA_BRIDGE` is authoritative: if it is wrong, the run fails rather than quietly building a
different catalogue.
