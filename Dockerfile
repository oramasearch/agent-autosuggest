# syntax=docker/dockerfile:1
# Auto-Suggest's service image (agent-autosuggest#1): private on Docker Hub as
# oramasearch/agent-autosuggest. The retrieval corpus is a named build context, `corpus`, pointing at
# agent-autosuggest-corpus/corpus, so the image carries the precedent it cannot run without.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --uid 10001 --create-home agent
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=catalog catalog.json catalog.source.json /app/catalog/
COPY --from=corpus autosuggest-sft-v3.jsonl /app/corpus/autosuggest-sft-v3.jsonl
ENV PATH=/app/.venv/bin:$PATH \
    ZETTA_CATALOG=/app/catalog/catalog.json \
    AUTOSUGGEST_CORPUS=/app/corpus/autosuggest-sft-v3.jsonl \
    PORT=8080
USER 10001
EXPOSE 8080
ENTRYPOINT ["agent-autosuggest-serve"]
