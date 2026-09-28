# Runtime App and build evidence

Auto-Suggest reads Ship App state directly from lake on every turn. These are lake control-plane
capabilities, so they use authenticated `GET /v1/<capability>` calls rather than the workspace
engine's `workspace_query`. Every call includes the turn's `customer_id`, and the task credential
is never written to a trace.

The read-only snapshot begins with `list_apps` and `list_builds`. For up to twelve returned Apps
and twelve builds, it follows with `get_app` or `get_build` and a non-blocking event read through
`receive_app_events` or `receive_build_events`. The event calls use `from_sequence: 1` and
`wait_ms: 0`; lake's retained cursor makes this a current-state snapshot rather than a background
watch. A record that disappears between list and get remains represented by its listed state and
the read error.

The evidence also carries the App and replica limits returned by lake. It contains no action
caller. App changes such as `create_app`, `deploy_app`, `build_app`, `rollback_app`, and `scale_app`
are vocabulary for a reviewed proposal only. ApplicationAgent (`agent-application`, turn kind
`app`) owns the later build or change after a person confirms the proposal.

An App Opportunity adds a `delivery` object to the ordinary solution Opportunity. `shows` states
the concrete dashboard or internal-tool contents. `agent`, `turn_kind`, `status`, and
`confirmation_required` bind the handoff to a confirmed ApplicationAgent turn. `proposed_actions`
contains only registered App or build action names. Auto-Suggest deterministically restores these
fields to `agent-application`, `app`, `proposed`, and `true`, and removes action names outside its
documented proposal vocabulary before validation.

The runtime prompt is `v4`. App contract cases in `evals/data-apps.jsonl` run through the same
enforcement and validation used by a service turn. The service trace includes the App/build
evidence but never the task credential.

If both list capabilities are unknown, Auto-Suggest records an empty compatibility snapshot. Other
list failures surface as turn failures because silently treating an authorization or store problem
as an empty workspace could create a duplicate App recommendation.
