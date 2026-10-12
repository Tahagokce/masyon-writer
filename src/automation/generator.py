"""Generate a reviewable Cucumber proposal without changing the Java repository."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .canonical import CanonicalAutomationModel
from .repository import Binding, expression_pattern, index_repository
from .proposals import locator_evidence, propose_click_step
from .validation import validate


@dataclass(frozen=True)
class GherkinStep:
    jiraStepNumber: int
    kind: str
    keyword: str
    text: str

    @property
    def line(self) -> str:
        return f"{self.keyword} {self.text}"


def _flat(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\u00a0", " ")
    value = re.sub(r":\s*\n\s*\*\s*", ": ", value)
    value = re.sub(r"\s*\n\s*\*\s*", "; ", value)
    value = re.sub(r"\s*\n\s*", " ", value)
    value = re.sub(r"^\s*\*\s+", "", value)
    value = re.sub(r"(?<!\w)\*(?=[\w“])", "", value)
    value = re.sub(r"(?<=[\w”])\*(?!\w)", "", value)
    value = re.sub(r"\s+ve;\s+", " ve ", value)
    return re.sub(r"\s+", " ", value).strip().rstrip(".")


def _assertion_description(assertion) -> str:
    name = assertion.target.get("name") or assertion.target.get("label") or assertion.target.get("role") or "Öğe"
    return {
        "VISIBLE": f"{name} görünür olduğu doğrulanır",
        "HIDDEN": f"{name} gizli olduğu doğrulanır",
        "CHECKED": f"{name} seçili olduğu doğrulanır",
        "SELECTED": f"{name} aktif olduğu doğrulanır",
        "ENABLED": f"{name} etkin olduğu doğrulanır",
        "DISABLED": f"{name} devre dışı olduğu doğrulanır",
        "TEXT": f"{name} metninin {_flat(assertion.expected or '')} olduğu doğrulanır",
        "VALUE": f"{name} değerinin {_flat(assertion.expected or '')} olduğu doğrulanır",
        "URL": f"Adresin {_flat(assertion.expected or '')} olduğu doğrulanır",
    }.get(assertion.type, f"{name} durumu doğrulanır")


def feature_steps(case: CanonicalAutomationModel) -> list[GherkinStep]:
    steps: list[GherkinStep] = []
    for canonical_step in case.steps:
        if canonical_step.actions or not canonical_step.assertions:
            action = _flat(canonical_step.description)
            if canonical_step.data:
                # Data stays in the Gherkin text, so it cannot silently disappear.
                values = "; ".join(f"{key}={value}" for key, value in canonical_step.data.items())
                action += f" (Test verisi: {_flat(values)})"
            steps.append(GherkinStep(canonical_step.stepNumber, "action",
                                     "Given" if canonical_step.stepNumber == 1 else "When", action))
        if canonical_step.expectedResult:
            steps.append(GherkinStep(canonical_step.stepNumber, "expectedResult", "Then",
                                     _flat(canonical_step.expectedResult)))
        else:
            for assertion in canonical_step.assertions:
                steps.append(GherkinStep(canonical_step.stepNumber, "expectedResult", "Then",
                                         _assertion_description(assertion)))
    return steps


def render_feature(case: CanonicalAutomationModel, steps: list[GherkinStep]) -> str:
    lines = [f"@{case.testCaseId}", f"Feature: {case.testCaseId}", "",
             f"  Scenario: {case.testCaseId} otomasyon adımları"]
    for step in steps:
        lines.append(f"    # Input step {step.jiraStepNumber}: {step.kind}")
        lines.append(f"    {step.line}")
    return "\n".join(lines) + "\n"


def scan_bindings(repository: Path) -> list[Binding]:
    return [Binding(**item) for item in index_repository(repository)["bindings"]]


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", _flat(text).casefold()).strip()


def _semantic_variant(text: str) -> str | None:
    # Small, explicit Turkish equivalence rule. Do not claim broad semantic
    # equivalence from string similarity.
    match = re.fullmatch(r"(?:Alt sekmelerden\s+)?(.+?)\s+sekmesine geçilir", text, re.IGNORECASE)
    if match:
        return f'"{match.group(1).strip()}" sekmesine tıklanır'
    return None


def match_binding(step: GherkinStep, bindings: list[Binding]) -> tuple[Binding | None, str | None, str]:
    target = step.text
    def matches(value: str) -> tuple[list[Binding], bool]:
        found = []
        uncertain = False
        for binding in bindings:
            pattern = expression_pattern(binding.expression)
            if pattern and pattern.fullmatch(value):
                found.append(binding)
            elif binding.expressionType == "LITERAL" and _normalized(binding.expression) == _normalized(value):
                found.append(binding)
            elif pattern is None and binding.expressionType == "CUCUMBER_EXPRESSION":
                parts = re.split(r"\{[^}]*\}", binding.expression)
                if value.startswith(parts[0]) and value.endswith(parts[-1]):
                    uncertain = True
        return found, uncertain
    direct, uncertain = matches(target)
    if len(direct) > 1 or uncertain:
        return None, "AMBIGUOUS", target
    if direct:
        binding = direct[0]
        kind = "EXACT_REUSE" if binding.expressionType == "LITERAL" or (
            binding.expressionType == "REGEX" and not re.search(r"\\[dws]|\([^?]", binding.expression)
        ) else "PARAMETERIZED_REUSE"
        return binding, kind, target
    variant = _semantic_variant(target)
    if variant:
        semantic, uncertain = matches(variant)
        if uncertain or len(semantic) > 1:
            return None, "AMBIGUOUS", target
        if len(semantic) == 1:
            return semantic[0], "SEMANTIC_REUSE", variant
    return None, "MISSING", target


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(case: CanonicalAutomationModel, folder: Path, automation_repo: Path | None = None,
             recordings: list[Path] | None = None, run_validation: bool = True) -> Path:
    """Write one proposal directory. ``automation_repo`` is strictly read-only."""
    if automation_repo and folder.resolve().is_relative_to(automation_repo.resolve()):
        raise ValueError("Proposal output must be outside the external automation repository")
    if folder.exists():
        raise FileExistsError(folder)
    steps = feature_steps(case)
    index = index_repository(automation_repo) if automation_repo else {
        "repository": None, "bindings": [], "pageObjects": [], "locators": [],
        "helpers": [], "stepClasses": [], "conventions": {}}
    bindings = [Binding(**item) for item in index["bindings"]]
    folder.mkdir(parents=True)
    _write_json(folder / "canonical-automation.json", case.to_dict())
    _write_json(folder / "automation-index.json", index)
    reuse, missing, unresolved, final_steps = [], [], [], []
    proposed = []
    proposed_by_text: dict[str, dict] = {}
    for step in steps:
        binding, match_type, selected_text = match_binding(step, bindings)
        selected = GherkinStep(step.jiraStepNumber, step.kind, step.keyword, selected_text)
        final_steps.append(selected)
        record = {"stepNumber": step.jiraStepNumber, "kind": step.kind,
                  "gherkin": selected.line, "status": match_type}
        if binding:
            record.update({"existingStep": binding.expression, "source": binding.source,
                           "method": binding.method, "expressionType": binding.expressionType})
        else:
            if match_type == "AMBIGUOUS":
                reason = "Multiple existing Cucumber bindings match; review required"
                unresolved.append({**record, "reason": reason})
            else:
                canonical_step = case.steps[step.jiraStepNumber - 1]
                evidence = locator_evidence(selected.text, index, recordings or [],
                                            canonical_step.locatorEvidence) if step.kind == "action" else {
                    "status": "NOT_APPLICABLE", "reason": "Assertion requires a verified application state method"}
                record["locatorEvidence"] = evidence
                if selected.text in proposed_by_text:
                    proposal = {**proposed_by_text[selected.text], "status": "PROPOSED_REUSE"}
                else:
                    proposal = ({"status": "UNRESOLVED", "reason": "Automation repository unavailable"}
                                if not automation_repo else
                                propose_click_step(selected.text, step.jiraStepNumber, evidence, index,
                                                   automation_repo, folder / "proposed-patch"))
                record["javaProposal"] = proposal
                missing.append(record)
                if proposal["status"] == "PROPOSED":
                    proposed.append(proposal)
                    proposed_by_text[selected.text] = proposal
                elif proposal["status"] == "PROPOSED_REUSE":
                    pass
                else:
                    unresolved.append({"stepNumber": step.jiraStepNumber, "kind": step.kind,
                                       "gherkin": selected.line, "locatorStatus": evidence["status"],
                                       "reason": proposal["reason"]})
        reuse.append(record)
    (folder / f"{case.testCaseId}.feature").write_text(render_feature(case, final_steps), encoding="utf-8")
    _write_json(folder / "reuse-report.json", reuse)
    _write_json(folder / "missing-steps.json", missing)
    _write_json(folder / "unresolved.json", unresolved)
    validation = validate(index, automation_repo, folder / "proposed-patch",
                          folder / f"{case.testCaseId}.feature", run_validation)
    validation["ambiguousGeneratedSteps"] = [r["gherkin"] for r in reuse if r["status"] == "AMBIGUOUS"]
    validation["candidateUndefinedSteps"] = [r["gherkin"] for r in missing
                                            if r["javaProposal"]["status"] not in {"PROPOSED", "PROPOSED_REUSE"}]
    _write_json(folder / "validation.json", validation)
    counts = {status: sum(r["status"] == status for r in reuse) for status in
              ("EXACT_REUSE", "PARAMETERIZED_REUSE", "SEMANTIC_REUSE", "MISSING", "AMBIGUOUS")}
    unresolved_locators = sum(r.get("locatorEvidence", {}).get("status") == "UNRESOLVED" for r in missing)
    report = [f"# {case.testCaseId} automation generation", "",
              f"- Source type: {case.sourceType}",
              f"- Canonical steps: {len(case.steps)}",
              f"- Total Gherkin bindings: {len(steps)}",
              f"- Exact reused: {counts['EXACT_REUSE']}",
              f"- Parameterized reused: {counts['PARAMETERIZED_REUSE']}",
              f"- Semantic reused: {counts['SEMANTIC_REUSE']}",
              f"- Missing: {counts['MISSING']}",
              f"- Ambiguous: {counts['AMBIGUOUS']}",
              f"- Unresolved locators: {unresolved_locators}",
              f"- Proposed Java bindings: {len(proposed)}",
              f"- Automation repository: {automation_repo or 'not supplied'}", "",
              *( ["Reuse, missing, undefined, and locator counts are provisional until the external repository is scanned.", ""]
                 if not automation_repo else []),
              "## Validation", "",
              f"- Duplicate definitions: {len(validation.get('duplicateDefinitions', {}))}",
              f"- Candidate undefined steps: {len(validation['candidateUndefinedSteps'])}",
              f"- Maven compile: {validation.get('mavenCompile', 'NOT_RUN')}",
              f"- Maven test: {validation.get('mavenTest', 'NOT_RUN')}",
              f"- Cucumber dry-run: {validation.get('cucumberDryRun', 'NOT_RUN')}", "",
              "The external automation repository was not modified. Proposed Java files, when provable, "
              "are under proposed-patch/ for review. Missing behavior without a proven Page Object helper "
              "or stable locator remains unresolved.", ""]
    (folder / "generation-report.md").write_text("\n".join(report), encoding="utf-8")
    return folder
