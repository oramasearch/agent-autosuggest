"""Runtime solution selection without a model or network."""

from __future__ import annotations

from agent_autosuggest import solutions, workspace

RUNNABLE = {
    "id": "S-3",
    "name": "Customer segmentation",
    "composition": [
        {"methodology": "M-3", "role": "primary", "minimum_level": 1},
        {"methodology": "M-6", "role": "attachable", "minimum_level": 2},
    ],
    "capability_levels": [
        {"level": 1, "name": "insight"},
        {"level": 2, "name": "recommend"},
    ],
    "required_data_roles": [
        {"role": "entity", "required": True, "description": "customer"},
    ],
    "missing_components": [],
    "status": "catalogue",
    "runnable": True,
    "issue": "",
    "promotion_condition": "",
}


class FakeRegistry:
    def __init__(self, entries):
        self.entries = {entry["id"]: entry for entry in entries}
        self.calls = []

    def query(self, capability, inputs):
        self.calls.append((capability, inputs))
        if capability == "list_solutions":
            return {"solutions": list(self.entries.values())}
        if capability == "get_solution":
            return {"solution": self.entries[inputs["solution"]]}
        raise AssertionError(f"unexpected capability {capability}")


def test_a_selected_solution_is_hydrated_and_uses_its_level_composition():
    registry = FakeRegistry([RUNNABLE])
    evidence = solutions.inspect(registry.query)
    answer = {
        "suggested": [
            {
                "intent": "segment customers",
                "solution": "S-3",
                "level": 2,
                "methodologies": ["made-up"],
                "runnable": False,
                "why_this_data_supports_it": "one row per customer",
            }
        ],
        "declined": [],
    }

    grounded = solutions.enforce(answer, evidence, {"feature_plans": []}, registry.query)

    opportunity = grounded["suggested"][0]
    assert opportunity["methodologies"] == ["M-3", "M-6"]
    assert opportunity["runnable"] is True
    assert registry.calls == [
        ("list_solutions", {}),
        ("get_solution", {"solution": "S-3"}),
    ]


def test_unknown_solution_capability_selects_the_legacy_path():
    def old_engine(capability, inputs):
        raise workspace.CapabilityError("unknown_capability", "not deployed")

    evidence = solutions.inspect(old_engine)

    assert not solutions.is_current(evidence)
    assert evidence["source"] == "old-engine fallback"
    assert "list_solutions" in evidence["reason"]


def test_deployed_field_aliases_are_read_without_defaults():
    solution = {
        "levels": [1, {"level": 3}],
        "composition": [
            {"id": "M-1", "minimum_level": 1},
            {"id": "M-8", "minimum_level": 3},
        ],
        "missing": [{"name": "policy", "issue": "zetta#1104"}],
    }

    assert solutions.available_levels(solution) == [1, 3]
    assert solutions.selected_methodologies(solution, 3) == ["M-1", "M-8"]
    assert solutions.missing_components(solution) == [
        {"name": "policy", "issue": "zetta#1104"}
    ]
