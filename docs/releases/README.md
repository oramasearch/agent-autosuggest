# Releases

One page per tagged release, newest first. A page states what the release includes, what it does
not include, known issues, verification output, and where the image runs. The first release lists
everything the agent does.

A release has one `vX.Y.Z` name shared by the Git tag, the GitHub release and the private Docker Hub
image tag `oramasearch/agent-autosuggest:vX.Y.Z`. The image tag names the digest already built from
the tag and deployed; it is never produced by a second build.

| Release | Date | Summary |
|---|---|---|
| [v0.1.0](v0.1.0.md) | 2026-10-17 | The first release: the Lake library agent that proposes Opportunities from a profile, over the agent bus and `POST /v1/turn`, with the model reached through Lake's gateway, prompt `v4`, and the private image |

`pyproject.toml`'s `[project].version` is the one place the version is set; `GET /healthz` reports
it, and `tests/test_release.py` fails when that version has no page here.
