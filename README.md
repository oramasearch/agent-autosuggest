# agent-autosuggest

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
export AUTOSUGGEST_CORPUS=../agent-autosuggest-corpus/corpus/autosuggest-sft-v1.jsonl
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
| `GET /healthz` | `200` with the corpus size, or `503` when the corpus is missing |
| `POST /v1/turn` | One suggestion. Body: `{"profile": {...}}` or `{"context": {"profile": {...}}}`, the profile carrying its tables or connections, optionally `request`. Answers `{ok, trace_id, result: {project_profile, few_shot_from, prompt_version, defects}}`; `422` with `error` otherwise |

It reads no node: lake builds the profile, so the agent sees only the situation it is handed.
`AGENT_SERVICE_TOKEN`, when set, is required as a bearer; every turn is a trace in `TRACE_DIR`;
`AUTOSUGGEST_MODEL` picks the model; `OPENAI_API_KEY` is the general Orama key the operator mounts.

**Image:** `Dockerfile`, published **private** as `oramasearch/agent-autosuggest`, with the
corpus as the named build context `corpus` (`agent-autosuggest-corpus/corpus`), built on the
cluster BuildKit (`--local corpus=../agent-autosuggest-corpus/corpus --opt
context:corpus=local:corpus`). Tests: `uv run pytest`.

## The three parts

| file | job |
|---|---|
| `retrieval.py` | TF-IDF nearest situations. Similarity is over the **situation**, never the answer — retrieving by answer shows the model what it is supposed to conclude. |
| `catalog.py` | The solutions catalogue as **context, not a tool**. Built from the plan markdown by `zetta/scripts/build_catalog.py`, never vendored. |
| `validate.py` | Rejects citations outside the catalogue and pipeline shapes that contradict their methodology. |

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
