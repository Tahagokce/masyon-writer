"""Evidence-gated Java proposals for missing Cucumber bindings."""

from __future__ import annotations

import re
from pathlib import Path

from .canonical import LocatorEvidence
from .repository import stable_id


def _tokens(value: str) -> list[str]:
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    return re.findall(r"[a-z0-9]+", value.casefold())


def locator_evidence(text: str, index: dict, recordings: list[Path],
                     canonical: list[LocatorEvidence] | None = None) -> dict:
    found = []
    for item in canonical or []:
        if item.strategy == "id" and (not isinstance(item.value, str) or not stable_id(item.value)):
            continue
        found.append({"source": item.provenance, "kind": item.strategy, "value": item.value,
                      "stable": item.strategy != "css", "confidence": item.confidence,
                      "origin": "canonical"})
    match = re.fullmatch(r"(.+?) butonuna tıklanır", text, re.I)
    target = _tokens(match.group(1)) if match else []
    expected = target + ["button"]
    if match:
        for locator in index["locators"]:
            if not locator["pageClass"] or not locator["stable"]:
                continue
            if _tokens(locator["field"]) in (target, expected) or _tokens(str(locator["value"])) in (target, expected):
                found.append({**locator, "confidence": "REPOSITORY"})
    # A recording is evidence only when it includes a successful action line.
    for recording in recordings:
        code = recording.read_text(encoding="utf-8")
        if "FAILED:" in code or "recording unavailable" in code:
            continue
        ids = re.findall(r'''page\.locator\(['"]#([^'"]+)['"]\)\.click\(''', code)
        ids += re.findall(r'''page\.getByTestId\(['"]([^'"]+)['"]\)\.click\(''', code)
        for value in ids:
            if match and stable_id(value) and _tokens(value) in (target, expected):
                found.append({"source": str(recording), "kind": "id-or-testid", "value": value,
                              "stable": True, "origin": "recording", "confidence": "RECORDED"})
        for role, name in re.findall(r'''page\.getByRole\(['"]([^'"]+)['"]\s*,\s*\{\s*name:\s*['"]([^'"]+)['"]\s*\}\)\.click\(''', code):
            if match and role.casefold() == "button" and _tokens(name) == target:
                found.append({"source": str(recording), "kind": "role+name", "value": name,
                              "stable": True, "origin": "recording", "confidence": "RECORDED"})
    priorities = {"PROVEN": 0, "RECORDED": 1, "REPOSITORY": 2, "INFERRED": 3}
    strategies = {"id": 0, "testId": 1, "role/name": 2, "label": 3,
                  "text": 4, "placeholder": 5, "css": 6}
    found = sorted(found, key=lambda x: (priorities.get(x["confidence"], 9),
                                        strategies.get(x["kind"], 9)))
    if found:
        preferred = found[0]
        status = "RESOLVED_PAGE_OBJECT" if preferred["confidence"] == "REPOSITORY" else "RESOLVED_EVIDENCE"
        return {"status": status, "locator": preferred, "all": found}
    return {"status": "UNRESOLVED", "reason": "No stable locator found in Page Objects or recordings"}


def propose_click_step(text: str, step_number: int, evidence: dict, index: dict,
                       repository: Path, proposal_root: Path) -> dict:
    """Add a binding only when an existing Page Object helper and DI field prove the call."""
    page_locators = [x for x in evidence.get("all", []) if x.get("confidence") == "REPOSITORY"]
    if len(page_locators) != 1:
        return {"status": "UNRESOLVED", "reason": "No existing Page Object helper can be wired safely"}
    match = re.fullmatch(r"(.+?) butonuna tıklanır", text, re.I)
    if not match:
        return {"status": "UNRESOLVED", "reason": "Java proposal supports verified button clicks only"}
    locator = page_locators[0]
    label = match.group(1)
    method_name = "click" + "".join(piece.title() for piece in _tokens(label)) + "Button"
    helpers = [h for h in index["helpers"] if h["className"] == locator["pageClass"] and
               h["name"].casefold() == method_name.casefold() and h["pageObject"] and
               h["visibility"] == "public" and h["zeroArguments"] and
               locator["field"] and locator["field"] in h["references"]]
    owners = [(step_class, field) for step_class in index["stepClasses"]
              for field in step_class["fields"] if field["type"] == locator["pageClass"] and field["injected"]]
    if len(helpers) != 1 or len(owners) != 1:
        return {"status": "UNRESOLVED", "reason": "Unique Page Object helper and injected step field were not proven"}
    step_class, field = owners[0]
    source_path = repository / step_class["source"]
    proposal_path = proposal_root / step_class["source"]
    original = proposal_path.read_text(encoding="utf-8") if proposal_path.exists() else source_path.read_text(encoding="utf-8")
    insert = original.rfind("}")
    if insert < 0:
        return {"status": "UNRESOLVED", "reason": "Step class closing brace could not be located"}
    binding_method = f"jiraStep{step_number}{''.join(piece.title() for piece in _tokens(label))}Click"
    if re.search(r"\b" + re.escape(binding_method) + r"\s*\(", original):
        return {"status": "UNRESOLVED", "reason": "Proposed Java method name already exists"}
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    addition = (f'\n    @io.cucumber.java.en.When("{escaped}")\n'
                f'    public void {binding_method}() {{\n'
                f'        {field["name"]}.{helpers[0]["name"]}();\n'
                f'    }}\n')
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(original[:insert] + addition + original[insert:], encoding="utf-8")
    return {"status": "PROPOSED", "file": str(proposal_path.relative_to(proposal_root)),
            "method": binding_method, "delegatesTo": f'{locator["pageClass"]}.{helpers[0]["name"]}'}
