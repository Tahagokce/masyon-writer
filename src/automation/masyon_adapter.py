"""Normalize MASYON's Jira handoff and execution artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

from src.jira.models import TestCase
from .canonical import (CanonicalAction, CanonicalAssertion, CanonicalAutomationModel,
                        CanonicalStep, LocatorEvidence, UnsupportedInputError)
from .repository import stable_id


def _data(value: str | dict) -> dict:
    if isinstance(value, dict):
        return value.copy()
    if not isinstance(value, str):
        raise UnsupportedInputError("MASYON Data must be text or an object")
    if not value.strip():
        return {}
    parsed, unmatched = {}, []
    for item in re.split(r"[\n;]+", value):
        match = re.fullmatch(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*?)\s*", item)
        if match:
            parsed[match.group(1)] = match.group(2)
        elif item.strip():
            unmatched.append(item.strip())
    if unmatched:
        parsed["_raw"] = value
    return parsed


def _inferred_action(description: str, step_number: int) -> list[CanonicalAction]:
    click = re.search(r"(.+?)\s+(butonuna|sekmesine)\s+tıklanır", description, re.I)
    if not click:
        click = re.search(r"(?:Alt sekmelerden\s+)?(.+?)\s+(sekmesine)\s+geçilir", description, re.I)
    if click:
        role = "button" if click.group(2).lower().startswith("buton") else "tab"
        name = re.sub(r"^Alt sekmelerden\s+", "", click.group(1).strip(), flags=re.I)
        return [CanonicalAction("CLICK", {"name": name, "role": role},
                                provenance={"method": "jira-wording", "stepNumber": step_number,
                                            "confidence": "INFERRED"})]
    return []


def _inferred_assertions(expected: str, step_number: int) -> list[CanonicalAssertion]:
    if not expected:
        return []
    provenance = {"method": "jira-wording", "stepNumber": step_number, "confidence": "INFERRED"}
    tab = re.search(r"(.+?)\s+sekmesinin\s+(?:açık|aktif)", expected, re.I)
    if tab:
        return [CanonicalAssertion("SELECTED", {"name": tab.group(1).strip(), "role": "tab"},
                                   provenance=provenance)]
    quoted = re.search(r"[“\"]([^”\"]+)[”\"]\s*mesajı", expected)
    if quoted:
        return [CanonicalAssertion("TEXT", {"role": "status"}, quoted.group(1), provenance)]
    dialog = re.search(r"(.+?)\s+popup[’']?ının\s+açıldığı", expected, re.I)
    if dialog:
        return [CanonicalAssertion("VISIBLE", {"name": dialog.group(1).strip(), "role": "dialog"},
                                   provenance=provenance)]
    screen = re.search(r"(.+?)\s+ekranının\s+açıldığı", expected, re.I)
    if screen:
        return [CanonicalAssertion("VISIBLE", {"name": screen.group(1).strip(), "role": "region"},
                                   provenance=provenance)]
    return []


def _locators(raw: list, step_number: int, executed: bool) -> list[LocatorEvidence]:
    if not isinstance(raw, list):
        raise UnsupportedInputError("MASYON locatorEvidence must be a list")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            raise UnsupportedInputError("MASYON locator evidence must be an object")
        strategy = item.get("strategy") or item.get("type")
        value = item.get("value")
        if strategy == "id" and (not isinstance(value, str) or not stable_id(value)):
            continue  # Keep raw source in sourceMetadata; do not rank dynamic IDs.
        confidence = item.get("confidence") or ("PROVEN" if executed else "INFERRED")
        result.append(LocatorEvidence(strategy, value, confidence,
                                      {"source": "MASYON", "stepNumber": step_number,
                                       **(item.get("provenance") or {})}))
    return result


class MasyonInputAdapter:
    source_type = "MASYON"

    def from_test_case(self, case: TestCase) -> CanonicalAutomationModel:
        return self.from_mapping(case.to_dict())

    def from_mapping(self, payload: dict, *, execution: dict | None = None,
                     source_path: str | None = None) -> CanonicalAutomationModel:
        if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
            raise UnsupportedInputError("MASYON input needs a steps array")
        case_id = payload.get("testCaseId") or payload.get("jiraKey")
        if not isinstance(case_id, str) or not case_id.strip():
            raise UnsupportedInputError("MASYON input needs testCaseId or jiraKey")
        executed_by_number = {item.get("stepNumber"): item for item in (execution or {}).get("steps", [])
                              if isinstance(item, dict)}
        steps = []
        for index, item in enumerate(payload["steps"], 1):
            if not isinstance(item, dict) or item.get("stepNumber") != index:
                raise UnsupportedInputError("MASYON step numbers must be consecutive")
            description = item.get("description") or item.get("action")
            if isinstance(description, dict):
                description = item.get("description")
            if not isinstance(description, str) or not description.strip():
                raise UnsupportedInputError(f"MASYON step {index} has no description")
            execution_step = executed_by_number.get(index, {})
            plan = item.get("semanticPlan") or item.get("plan") or {}
            if plan and not isinstance(plan, dict):
                raise UnsupportedInputError("MASYON semanticPlan must be an object")
            raw_actions = plan.get("actions", item.get("actions", []))
            if not raw_actions and isinstance(item.get("action"), dict):
                raw_actions = [item["action"]]
            if not isinstance(raw_actions, list):
                raise UnsupportedInputError("MASYON actions must be a list")
            if any(not isinstance(action, dict) or "type" not in action for action in raw_actions):
                raise UnsupportedInputError("MASYON action needs a type")
            actions = [CanonicalAction(str(action["type"]).upper(),
                                       action.get("target") or item.get("target") or {},
                                       action.get("value"), action.get("key"), action.get("url"),
                                       action.get("pageAlias") or item.get("pageAlias"),
                                       {"source": "MASYON", "stepNumber": index,
                                        **(action.get("provenance") or {})})
                       for action in raw_actions]
            if not actions:
                actions = _inferred_action(description, index)
            raw_assertions = plan.get("expectedAssertions", item.get("expectedAssertions", item.get("assertions", [])))
            if not isinstance(raw_assertions, list):
                raise UnsupportedInputError("MASYON assertions must be a list")
            if any(not isinstance(assertion, dict) or "type" not in assertion for assertion in raw_assertions):
                raise UnsupportedInputError("MASYON assertion needs a type")
            assertions = [CanonicalAssertion(str(assertion["type"]).upper(),
                                             assertion.get("target") or {}, assertion.get("expected"),
                                             {"source": "MASYON", "stepNumber": index,
                                              **(assertion.get("provenance") or {})})
                          for assertion in raw_assertions]
            expected = item.get("expectedResult") or ""
            if not assertions:
                assertions = _inferred_assertions(expected, index)
            raw_locators = item.get("locatorEvidence") or []
            plan_locators = plan.get("locatorEvidence") or []
            if not isinstance(raw_locators, list) or not isinstance(plan_locators, list):
                raise UnsupportedInputError("MASYON locatorEvidence must be a list")
            raw_locators = raw_locators + plan_locators
            for action in raw_actions:
                action_locators = action.get("locatorEvidence") or []
                if not isinstance(action_locators, list):
                    raise UnsupportedInputError("MASYON action locatorEvidence must be a list")
                raw_locators += action_locators
            locators = _locators(raw_locators, index, execution_step.get("status") == "COMPLETED")
            page_data = dict(item.get("page") or {})
            if item.get("popup"):
                page_data["popup"] = item["popup"]
            if not page_data and execution_step.get("activePage"):
                page_data["alias"] = execution_step["activePage"]
            steps.append(CanonicalStep(index, description, actions, item.get("target") or {},
                                       _data(item.get("data") or {}), locators, assertions, expected,
                                       page_data,
                                       {"original": item, "execution": execution_step}))
        return CanonicalAutomationModel(case_id, "MASYON", steps,
                                        {"sourcePath": source_path, "originalTopLevel":
                                         {k: v for k, v in payload.items() if k != "steps"},
                                         "execution": execution or {}})

    def read(self, path: Path) -> CanonicalAutomationModel:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise UnsupportedInputError("Invalid MASYON JSON input") from exc
        if not isinstance(payload, dict):
            raise UnsupportedInputError("MASYON input must be a JSON object")
        if path.name == "execution.json" or ("status" in payload and "jiraKey" in payload and "steps" in payload):
            companion = path.with_name("normalized-test-case.json")
            if not companion.is_file():
                raise UnsupportedInputError("MASYON execution.json needs sibling normalized-test-case.json")
            try:
                normalized = json.loads(companion.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise UnsupportedInputError("Invalid companion MASYON JSON input") from exc
            model = self.from_mapping(normalized, execution=payload, source_path=str(path))
        else:
            model = self.from_mapping(payload, source_path=str(path))
        recording = path.with_name("recording.spec.ts")
        if recording.is_file():
            content = recording.read_bytes()
            metadata = {**model.sourceMetadata, "recordingArtifact": {
                "path": str(recording), "sha256": hashlib.sha256(content).hexdigest(),
                "locatorAssignment": "UNASSIGNED_WITHOUT_STEP_MAPPING"}}
            model = replace(model, sourceMetadata=metadata)
        return model
