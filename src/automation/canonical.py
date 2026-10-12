"""Source-neutral automation input consumed by the writer pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ACTION_TYPES = {"CLICK", "FILL", "SELECT", "CHECK", "UNCHECK", "PRESS",
                "NAVIGATE", "WAIT", "SWITCH_PAGE"}
ASSERTION_TYPES = {"VISIBLE", "HIDDEN", "TEXT", "VALUE", "CHECKED", "UNCHECKED",
                   "SELECTED", "UNSELECTED", "ENABLED", "DISABLED", "URL"}
LOCATOR_STRATEGIES = {"id", "testId", "role/name", "label", "text", "placeholder", "css"}
CONFIDENCES = {"PROVEN", "RECORDED", "INFERRED"}
SOURCES = {"MASYON", "PLAYWRIGHT_RECORDING"}


class UnsupportedInputError(ValueError):
    """Input cannot be normalized without guessing; surfaced as UNSUPPORTED_INPUT."""

    code = "UNSUPPORTED_INPUT"


@dataclass(frozen=True)
class CanonicalAction:
    type: str
    target: dict[str, Any] = field(default_factory=dict)
    value: str | None = None
    key: str | None = None
    url: str | None = None
    pageAlias: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.type not in ACTION_TYPES:
            raise UnsupportedInputError(f"Unsupported action type: {self.type}")


@dataclass(frozen=True)
class CanonicalAssertion:
    type: str
    target: dict[str, Any] = field(default_factory=dict)
    expected: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.type not in ASSERTION_TYPES:
            raise UnsupportedInputError(f"Unsupported assertion type: {self.type}")


@dataclass(frozen=True)
class LocatorEvidence:
    strategy: str
    value: str | dict[str, str]
    confidence: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.strategy not in LOCATOR_STRATEGIES or self.confidence not in CONFIDENCES:
            raise UnsupportedInputError("Unsupported locator strategy or confidence")


@dataclass(frozen=True)
class CanonicalStep:
    stepNumber: int
    description: str
    actions: list[CanonicalAction] = field(default_factory=list)
    target: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    locatorEvidence: list[LocatorEvidence] = field(default_factory=list)
    assertions: list[CanonicalAssertion] = field(default_factory=list)
    expectedResult: str = ""
    page: dict[str, Any] = field(default_factory=dict)
    sourceMetadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.stepNumber < 1 or not self.description.strip():
            raise UnsupportedInputError("Each canonical step needs a positive number and description")


@dataclass(frozen=True)
class CanonicalAutomationModel:
    testCaseId: str
    sourceType: str
    steps: list[CanonicalStep]
    sourceMetadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.sourceType not in SOURCES or not self.testCaseId.strip() or not self.steps:
            raise UnsupportedInputError("Canonical model needs source type, test case ID, and steps")
        if [step.stepNumber for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise UnsupportedInputError("Canonical step numbers must be consecutive from 1")

    def to_dict(self) -> dict[str, Any]:
        return {"modelVersion": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CanonicalAutomationModel":
        if not isinstance(payload, dict) or payload.get("modelVersion") != 1:
            raise UnsupportedInputError("Unsupported canonical model version")
        try:
            steps = []
            for raw in payload["steps"]:
                steps.append(CanonicalStep(
                    raw["stepNumber"], raw["description"],
                    [CanonicalAction(**item) for item in raw.get("actions", [])],
                    raw.get("target") or {}, raw.get("data") or {},
                    [LocatorEvidence(**item) for item in raw.get("locatorEvidence", [])],
                    [CanonicalAssertion(**item) for item in raw.get("assertions", [])],
                    raw.get("expectedResult") or "", raw.get("page") or {},
                    raw.get("sourceMetadata") or {}))
            return cls(payload["testCaseId"], payload["sourceType"], steps,
                       payload.get("sourceMetadata") or {})
        except (KeyError, TypeError, AttributeError) as exc:
            raise UnsupportedInputError("Malformed canonical automation model") from exc
