# Runtime methodologies

Auto-Suggest bases Opportunities on the methodology registry in the customer's workspace. For
each turn it calls lake's `workspace_query` with `customer_id`, `capability`, and `input`, beginning
with `list_methodologies`. The returned list is not cached because a methodology can become
runnable without an Auto-Suggest release. The bus worker carries the claimed task's `customer_id`
into the turn alongside its task credential so the query stays scoped to that workspace.

The agent calls `analyze_dataset` for every dataset in the profile. It then calls `plan_features`
for each dataset and each currently runnable methodology. These results are model context and are
also enforced after generation:

- a runnable suggestion carries its `M-n` methodology, catalogue `S-n` solution, and `runnable:
  true`;
- an opportunity for an unavailable methodology is moved to `declined`, carries `runnable: false`,
  and names the registry's tracking issue;
- when every available feature plan requires preparation, the opportunity is moved to `declined`
  with `transform first` and the transform instruction.

If `list_methodologies` returns `unknown_capability`, the workspace predates the registry. The
agent uses the compatibility fact that only M-1, churn / GBDT classification, is runnable and says
that the old-engine fallback is active. A direct invocation without workspace query credentials
uses the same conservative fallback.

The runtime prompt is `v5` (see [`../solutions/runtime-solutions.md`](../solutions/runtime-solutions.md) for what v5 changed, lake#1153). This methodology contract remains the compatibility path when
`list_solutions` is unavailable. On a current engine the solution registry composes multiple
methodologies, as documented in
[`../solutions/runtime-solutions.md`](../solutions/runtime-solutions.md).

## Legacy Opportunity contract

On the compatibility path, both `suggested` and `declined` records include `solution`,
`methodology`, and `runnable`. Decline answers can be `capture it first`, `cannot be done`, `not
supported yet`, or `transform first`. Unsupported reasons include the methodology id and registry
issue. Transform reasons begin with `transform first:` and state the required data preparation.
