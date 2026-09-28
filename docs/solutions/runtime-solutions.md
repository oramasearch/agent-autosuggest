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

If `list_solutions` answers `unknown_capability`, Auto-Suggest uses the legacy runtime-methodology
contract documented in [`../methodologies/runtime-methodologies.md`](../methodologies/runtime-methodologies.md).
That compatibility path preserves the single `methodology` field and the conservative M-1 fallback.
