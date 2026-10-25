# Runtime solutions

Auto-Suggest reads the customer's solution registry through lake's `workspace_query` on every
turn. `list_solutions` supplies the catalogue considered by the model. After the model selects an
entry, `get_solution` supplies the full record used for enforcement. The selected detail is kept
only in that turn's evidence and trace; a later turn reads the registry again.

Zetta currently names detailed fields `capability_levels`, `required_data_roles`, and
`missing_components`. Auto-Suggest also accepts the shorter `levels`, `data_roles`, and `missing`
spellings described by the capability contract so a field rename does not make runtime status look
more capable than it is.

At a selected level, an Opportunity's `methodologies` are the registry composition entries whose
`minimum_level` is at or below that level. The registry's `runnable` value is authoritative.
Runnable entries can remain suggested. Entries with missing foundation components are declined as
`not supported yet` and say that the pipeline builds while production operation still needs the
named components and issues. Band-B entries with `status: sketch` are declined with their promotion
condition and cannot be presented as buildable.

Feature preparation still comes from the methodology evidence. If any methodology selected at the
chosen level requires a transform for every candidate dataset, the whole solution is declined with
`transform first` and the concrete preparation instruction.

**The feature plans decide whether the data is ready** ([lake#1153](https://github.com/oramasearch/lake/issues/1153)).
On lake-dev, prompt v4 declined every clean churn table as `transform first`, asking for an
"approved snapshot/as_of setup" that Lake offers no way to make, so *Scan my workspace* suggested
nothing. Now:

- **A ready composition is an Opportunity.** When the model declines a runnable solution as
  `transform first` while every methodology composed at its level plans `ready` on one dataset,
  `solutions.enforce` suggests it instead. The model's preparation becomes a step of the project
  (`required_first`), and the grounding names the dataset and the cutoff lake read from it
  (`inferred.as_of`, lake's `docs/api/feature-planning.md`).
- **A transform names the Lake capability that performs it.** The engine's real answer is
  `status: transform_required` with `transform_required: {name, description, ...}`; lake adds
  `performed_by`. The decline's reason is `transform first: <name>: <description>. Lake does it with
  <capability> (<what it does>); ...`. With a lake that does not answer `performed_by`, the agent
  names the same capabilities itself (`methodologies.performed_by`).
- **A transform first that names no Lake capability is a defect** (`validate.py`), handed back to the
  model in the one repair round: `plan_features`, `upload_dataset`, `workspace_sql`,
  `create_dataset_from_query` or `generate_dataset`.
- The model sees each feature plan without the copy of its dataset's analysis (already listed once
  under `analyses`).

Tests: `tests/test_lake_1153_shapes.py`, one case per shape in the issue.

The runtime prompt is `v5` (`prompts/v5.md`: a ready composition is suggested with its preparation in
`required_first`; a `time` role is met by a snapshot column lake reads or an `as_of` declared at
training; a transform first names its Lake capability). Deterministic cases in `evals/runtime-solutions.jsonl` cover runnable,
missing-foundation, sketch, and transform-first outputs. The test suite applies the same enforcement
and validation path used by a service turn to every case.

If `list_solutions` answers `unknown_capability`, Auto-Suggest uses the legacy runtime-methodology
contract documented in [`../methodologies/runtime-methodologies.md`](../methodologies/runtime-methodologies.md).
That compatibility path preserves the single `methodology` field and the conservative M-1 fallback.
