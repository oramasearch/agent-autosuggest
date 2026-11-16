# Releases

One page per tagged release, newest first. A page states what the release includes, what it does
not include, known issues, verification output, and where the image runs. The first release lists
everything the agent does.

A release has one `vX.Y.Z` name shared by the Git tag, the GitHub release and the Docker Hub
image tag `oramasearch/agent-autosuggest:vX.Y.Z`. The image tag names the digest already built from
the tag and deployed; it is never produced by a second build.

| Release | Date | Summary |
|---|---|---|
| [v0.3.1](v0.3.1.md) | 2026-11-16 | A patch: the README cleaned of internal references; the agent is unchanged |
| [v0.3.0](v0.3.0.md) | 2026-11-16 | The MIT licence, a security policy and the public-export preparation; a bus task claimed only when a worker is free; the image bases pinned by digest |
| [v0.2.0](v0.2.0.md) | 2026-10-25 | A minor release: ready churn data is suggested instead of declined (prompt `v5`), and the security-review fixes: constant-time service token refused when unset, a turn's credential only to the service's own `LAKE_URL`, no turn trace on disk |
| [v0.1.0](v0.1.0.md) | 2026-10-17 | The first release: the Lake library agent that proposes Opportunities from a profile, over the agent bus and `POST /v1/turn`, with the model reached through Lake's gateway, prompt `v4`, and the image |

`pyproject.toml`'s `[project].version` is the one place the version is set; `GET /healthz` reports
it, and `tests/test_release.py` fails when that version has no page here.
