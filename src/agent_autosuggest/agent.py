"""Auto-Suggest: a profile in, a project profile out.

No trained model yet — a frontier model with few-shot examples retrieved from the training corpus.
That corpus becomes SFT data later; until then it is the precedent this agent reasons from, which
also makes it continuously exercised rather than sitting unused until a training run.

The output is the same shape `agent-project` consumes, so the two compose without a translation
layer that can drift.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from agent_autosuggest import catalog, methodologies, solutions, validate
from agent_autosuggest.retrieval import Corpus

PROMPT_VERSION = "v3"
PROMPTS = Path(__file__).parent / "prompts"


def _shots(examples: list[dict[str, Any]]) -> str:
    """Render retrieved examples as precedent. Situation and answer together — the model needs to
    see what a good answer looked like FOR a situation, not an answer floating free."""
    if not examples:
        return ""
    blocks = []
    for example in examples:
        profile = {k: v for k, v in example.items()
                   if k in {"industry", "persona", "connections", "tables"}}
        blocks.append("SITUATION:\n" + json.dumps(profile, indent=1)
                      + "\nGOOD ANSWER:\n" + json.dumps(example.get("project_profile"), indent=1))
    return ("\n\nWorked examples from similar situations — precedent, not a template:\n\n"
            + "\n\n---\n\n".join(blocks))


def suggest(profile: dict[str, Any], corpus: Corpus, client: OpenAI, model: str,
            k: int = 3,
            methodology_evidence: dict[str, Any] | None = None,
            solution_evidence: dict[str, Any] | None = None,
            query: methodologies.Query | None = None,
            ) -> tuple[dict[str, Any] | None, list[str]]:
    """Return the project profile and which examples informed it."""
    methodology_evidence = methodology_evidence or methodologies.inspect(profile, None)
    solution_evidence = solution_evidence or solutions.inspect(None)
    neighbours = corpus.nearest(profile, k)
    system = ((PROMPTS / f"{PROMPT_VERSION}.md").read_text()
              + catalog.as_context()
              + solutions.as_context(solution_evidence)
              + methodologies.as_context(methodology_evidence)
              + _shots(neighbours))
    messages: list[dict[str, Any]] = [{"role": "system", "content": system},
                                      {"role": "user", "content": json.dumps(profile)}]

    # One repair round. Registry contradictions are mechanical, so handing them back is enough; a
    # second round would be the model guessing rather than correcting, and an answer that fails
    # twice should surface as failed.
    answer = None
    for attempt in range(2):
        text = _parse(client.chat.completions.create(
            model=model, messages=messages).choices[0].message.content)
        if text is None:
            break
        answer = solutions.enforce(
            text,
            solution_evidence,
            methodology_evidence,
            query,
        )
        problems = validate.defects(answer, methodology_evidence, solution_evidence)
        if not problems:
            return answer, [e.get("profile_id", "?") for e in neighbours]
        if attempt == 0:
            messages += [{"role": "assistant", "content": json.dumps(answer)},
                         {"role": "user", "content": "Fix these and return the whole object again:\n"
                          + "\n".join(f"- {p}" for p in problems)}]
    return answer, [e.get("profile_id", "?") for e in neighbours]


def _parse(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    body = fence.group(1) if fence else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Suggest what to build from a profile.")
    parser.add_argument("profile", type=Path, help="a profile.json")
    parser.add_argument("--corpus", type=Path,
                        default=Path(os.environ.get("AUTOSUGGEST_CORPUS", "")))
    parser.add_argument("--model", default=os.environ.get("AUTOSUGGEST_MODEL", "gpt-4o"))
    parser.add_argument("--shots", type=int, default=3)
    args = parser.parse_args()

    profile = json.loads(args.profile.read_text())
    corpus = Corpus(args.corpus)
    if not corpus.examples:
        # Say it rather than silently answering zero-shot. A run with no precedent is a different
        # experiment from one with three, and conflating them makes the result unreadable.
        print(f"note: no corpus at {args.corpus} — answering zero-shot")

    answer, used = suggest(profile, corpus, OpenAI(), args.model, args.shots)
    if answer is None:
        print("no parseable project profile")
        return 1

    problems = validate.defects(answer)
    print(json.dumps({"project_profile": answer,
                      "few_shot_from": used,
                      "prompt_version": PROMPT_VERSION,
                      "defects": problems}, indent=2))
    # A defective answer still prints — the caller should see what was wrong with it — but the exit
    # code says not to trust it.
    return 2 if problems else 0
