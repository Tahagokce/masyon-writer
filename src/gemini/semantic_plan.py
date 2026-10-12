"""Validate the planner's intent before it reaches the browser executor."""

ACTION_TYPES = {"click", "fill", "select", "check", "uncheck", "press", "navigate"}
ASSERTION_TYPES = {"visible", "hidden", "text", "value", "enabled", "disabled",
                   "checked", "unchecked", "selected", "unselected", "url"}
TARGET_FIELDS = {"name", "roleHint", "label", "text", "placeholder"}


def _target(value):
    if not isinstance(value, dict) or not value or set(value) - TARGET_FIELDS:
        raise ValueError("Semantic target must contain only supported descriptive fields")
    if not any(isinstance(item, str) and item.strip() for item in value.values()):
        raise ValueError("Semantic target needs a name, role hint, label, text, or placeholder")
    if any(not isinstance(item, str) or len(item) > 240 for item in value.values()):
        raise ValueError("Semantic target fields must be short strings")
    return value


def validate_plan(plan, expected_result):
    if not isinstance(plan, dict) or set(plan) - {"actions", "expectedAssertions", "generatedValues"}:
        raise ValueError("Planner must return actions and expectedAssertions")
    actions, assertions = plan.get("actions"), plan.get("expectedAssertions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 10:
        raise ValueError("Semantic plan requires 1–10 actions")
    if not isinstance(assertions, list) or len(assertions) > 10 or (expected_result and not assertions):
        raise ValueError("Expected result requires at least one semantic assertion")
    output = []
    for action in actions:
        if not isinstance(action, dict) or set(action) - {"type", "target", "value", "key", "url"}:
            raise ValueError("Unsupported semantic action fields")
        kind = action.get("type")
        if kind not in ACTION_TYPES:
            raise ValueError("Unsupported semantic action type")
        if kind not in {"navigate", "press"} or "target" in action:
            _target(action.get("target"))
        if kind in {"fill", "select"} and not isinstance(action.get("value"), str):
            raise ValueError("Fill/select actions require a string value")
        if kind == "press" and not isinstance(action.get("key"), str):
            raise ValueError("Press actions require a key")
        if kind == "navigate" and not isinstance(action.get("url"), str):
            raise ValueError("Navigate actions require a URL")
        output.append(action)
    for assertion in assertions:
        if not isinstance(assertion, dict) or set(assertion) - {"type", "target", "expected"}:
            raise ValueError("Unsupported semantic assertion fields")
        kind = assertion.get("type")
        if kind not in ASSERTION_TYPES:
            raise ValueError("Unsupported semantic assertion type")
        if kind != "url":
            _target(assertion.get("target"))
        if kind in {"text", "value", "url"} and not isinstance(assertion.get("expected"), str):
            raise ValueError("Text, value, and URL assertions require expected text")
        output.append({"type": "assert", "kind": kind,
                       **({"target": assertion["target"]} if "target" in assertion else {}),
                       **({"expected": assertion["expected"]} if "expected" in assertion else {})})
    if len(output) > 10:
        raise ValueError("Combined semantic plan cannot exceed 10 actions")
    return output
