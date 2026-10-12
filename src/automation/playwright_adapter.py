"""Small, fail-closed parser for common manual Playwright action recordings."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .canonical import (CanonicalAction, CanonicalAssertion, CanonicalAutomationModel,
                        CanonicalStep, LocatorEvidence, UnsupportedInputError)
from .repository import stable_id


SCAFFOLD = re.compile(r"^(?:import\s+.+;?|(?:test|test\.describe)\s*\(.+=>\s*\{|\}\);?|\};?|\{|\})$")
ACTION = re.compile(r"^await\s+([A-Za-z_$][\w$]*\..+)\.(click|fill|selectOption|check|uncheck|press)\((.*)\);?$", re.S)
ASSERT = re.compile(r"^await\s+expect\((.+)\)\.(toBeVisible|toBeHidden|toHaveText|toContainText|toHaveValue|toBeChecked|toBeSelected|toBeEnabled|toBeDisabled|toHaveURL)\((.*)\);?$", re.S)
LOCATOR = re.compile(r"^page\.(locator|getByRole|getByLabel|getByText|getByPlaceholder|getByTestId)\((.*)\)$", re.S)
STRATEGY_ORDER = {"id": 0, "testId": 1, "role/name": 2, "label": 3, "text": 4, "placeholder": 5, "css": 6}


def _split(value: str) -> list[str]:
    parts, start, depth, quote, escaped = [], 0, 0, None, False
    for index, char in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
            if depth < 0:
                raise UnsupportedInputError("Unbalanced Playwright expression")
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    if quote or depth:
        raise UnsupportedInputError("Unbalanced Playwright expression")
    parts.append(value[start:].strip())
    return parts


def _literal(value: str) -> str:
    try:
        result = ast.literal_eval(value.strip())
    except (ValueError, SyntaxError) as exc:
        raise UnsupportedInputError("Only literal Playwright string arguments are supported") from exc
    if not isinstance(result, str):
        raise UnsupportedInputError("Only literal Playwright string arguments are supported")
    return result


def _locator(expression: str, line: int, aliases: set[str]) -> tuple[dict, LocatorEvidence, str]:
    alias, dot, rest = expression.strip().partition(".")
    if not dot or alias not in aliases:
        raise UnsupportedInputError(f"Line {line}: unknown page alias")
    match = LOCATOR.fullmatch("page." + rest)
    if not match:
        raise UnsupportedInputError(f"Line {line}: unsupported Playwright locator")
    method, arguments = match.groups()
    args = _split(arguments)
    provenance = {"source": "PLAYWRIGHT_RECORDING", "line": line, "expression": expression.strip()}
    if method == "getByRole":
        if len(args) not in {1, 2}:
            raise UnsupportedInputError(f"Line {line}: unsupported getByRole options")
        role = _literal(args[0])
        name = None
        if len(args) == 2:
            option = args[1]
            if not option.startswith("{") or not option.endswith("}"):
                raise UnsupportedInputError(f"Line {line}: unsupported getByRole options")
            pairs = _split(option[1:-1])
            for pair in pairs:
                key, sep, raw = pair.partition(":")
                if not sep or key.strip() not in {"name", "exact"}:
                    raise UnsupportedInputError(f"Line {line}: unsupported getByRole option")
                if key.strip() == "name":
                    name = _literal(raw)
                elif raw.strip() not in {"true", "false"}:
                    raise UnsupportedInputError(f"Line {line}: unsupported exact option")
        target = {"role": role}
        if name is not None:
            target["name"] = name
        return target, LocatorEvidence("role/name", target.copy(), "RECORDED", provenance), alias
    if len(args) != 1:
        raise UnsupportedInputError(f"Line {line}: unsupported {method} arguments")
    value = _literal(args[0])
    if method == "locator":
        if value.startswith("#"):
            identifier = value[1:]
            if not stable_id(identifier):
                raise UnsupportedInputError(f"Line {line}: dynamic or invalid DOM ID")
            return {"id": identifier}, LocatorEvidence("id", identifier, "RECORDED", provenance), alias
        test_id = re.fullmatch(r'''\[data-testid\s*=\s*['"]([^'"]+)['"]\]''', value)
        if test_id:
            if not stable_id(test_id.group(1)):
                raise UnsupportedInputError(f"Line {line}: dynamic test ID")
            return {"testId": test_id.group(1)}, LocatorEvidence("testId", test_id.group(1), "RECORDED", provenance), alias
        if not value or re.search(r":nth-|:has\(|:visible|>>", value):
            raise UnsupportedInputError(f"Line {line}: unsupported or brittle CSS locator")
        return {"css": value}, LocatorEvidence("css", value, "RECORDED", provenance), alias
    strategy = {"getByLabel": "label", "getByText": "text", "getByPlaceholder": "placeholder",
                "getByTestId": "testId"}[method]
    if strategy == "testId" and not stable_id(value):
        raise UnsupportedInputError(f"Line {line}: dynamic test ID")
    target = {strategy: value}
    if strategy in {"label", "text", "placeholder"}:
        target["name"] = value
    return target, LocatorEvidence(strategy, value, "RECORDED", provenance), alias


def _description(kind: str, target: dict, step_number: int) -> str:
    name = target.get("name") or target.get("label") or target.get("placeholder")
    if kind == "CLICK":
        if name and target.get("role") == "button":
            return f"{name} butonuna tıklanır"
        if name and target.get("role") == "tab":
            return f"{name} sekmesine tıklanır"
        return f"{name} öğesine tıklanır" if name else f"Kayıttaki {step_number}. arayüz öğesine tıklanır"
    if kind == "FILL":
        return f"{name} alanı doldurulur" if name else f"Kayıttaki {step_number}. arayüz alanı doldurulur"
    if kind == "SELECT":
        return f"{name} seçilir" if name else f"Kayıttaki {step_number}. arayüz seçeneği seçilir"
    return f"{name or f'Kayıttaki {step_number}. arayüz öğesi'} için {kind.lower()} uygulanır"


class PlaywrightInputAdapter:
    source_type = "PLAYWRIGHT_RECORDING"

    def read(self, path: Path, test_case_id: str | None = None) -> CanonicalAutomationModel:
        return self.from_text(path.read_text(encoding="utf-8"), source_path=str(path),
                              test_case_id=test_case_id or (re.search(r"[A-Z]+-\d+", path.stem.upper()).group()
                                                       if re.search(r"[A-Z]+-\d+", path.stem.upper()) else
                                                       "MANUAL-" + re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-").upper()))

    def from_text(self, text: str, *, source_path: str = "<memory>",
                  test_case_id: str = "MANUAL-RECORDING") -> CanonicalAutomationModel:
        steps: list[CanonicalStep] = []
        aliases = {"page"}
        for line_number, original in enumerate(text.splitlines(), 1):
            line = original.strip()
            if not line or line.startswith("//") or SCAFFOLD.fullmatch(line):
                continue
            popup = re.fullmatch(r'''const\s+([A-Za-z_$][\w$]*)\s*=\s*await\s+(page|context)\.waitForEvent\(['"](popup|page)['"]\);?''', line)
            if popup:
                alias, owner, event = popup.groups()
                if alias in aliases:
                    raise UnsupportedInputError(f"Line {line_number}: page alias already exists")
                aliases.add(alias)
                action = CanonicalAction("SWITCH_PAGE", {"event": event}, pageAlias=alias,
                                         provenance={"sourcePath": source_path, "line": line_number,
                                                     "owner": owner})
                steps.append(CanonicalStep(len(steps)+1, "Yeni sayfaya geçilir", [action],
                                           {"event": event}, page={"alias": alias, "opener": owner},
                                           sourceMetadata={"line": line_number, "raw": original}))
                continue
            if line.startswith("await page.goto(") and line.endswith(");"):
                url = _literal(line[len("await page.goto("):-2])
                action = CanonicalAction("NAVIGATE", {"url": url}, url=url,
                                         provenance={"sourcePath": source_path, "line": line_number})
                steps.append(CanonicalStep(len(steps)+1, "Uygulama adresine gidilir", [action],
                                           {"url": url}, sourceMetadata={"line": line_number, "raw": original}))
                continue
            if line.startswith("await page.waitForTimeout(") and line.endswith(");"):
                value = line[len("await page.waitForTimeout("):-2]
                if not re.fullmatch(r"\d+", value):
                    raise UnsupportedInputError(f"Line {line_number}: unsupported wait duration")
                action = CanonicalAction("WAIT", value=value,
                                         provenance={"sourcePath": source_path, "line": line_number})
                steps.append(CanonicalStep(len(steps)+1, "Belirtilen süre beklenir", [action],
                                           data={"milliseconds": value}, sourceMetadata={"line": line_number, "raw": original}))
                continue
            match = ASSERT.fullmatch(line)
            if match:
                expression, method, raw_args = match.groups()
                if method == "toHaveURL" and expression.strip() in aliases:
                    target, evidence, alias = {"role": "page"}, None, expression.strip()
                else:
                    target, evidence, alias = _locator(expression, line_number, aliases)
                assertion_type = {"toBeVisible": "VISIBLE", "toBeHidden": "HIDDEN",
                                  "toHaveText": "TEXT", "toContainText": "TEXT", "toHaveValue": "VALUE",
                                  "toBeChecked": "CHECKED", "toBeSelected": "SELECTED",
                                  "toBeEnabled": "ENABLED", "toBeDisabled": "DISABLED", "toHaveURL": "URL"}[method]
                expected = _literal(raw_args) if assertion_type in {"TEXT", "VALUE", "URL"} else None
                if expected is None and raw_args.strip():
                    raise UnsupportedInputError(f"Line {line_number}: unsupported assertion options")
                assertion = CanonicalAssertion(assertion_type, target, expected,
                                               {"sourcePath": source_path, "line": line_number,
                                                "method": method, "pageAlias": alias})
                if steps:
                    previous = steps[-1]
                    steps[-1] = CanonicalStep(previous.stepNumber, previous.description, previous.actions,
                                              previous.target, previous.data,
                                              previous.locatorEvidence + ([evidence] if evidence else []),
                                              previous.assertions + [assertion], previous.expectedResult,
                                              previous.page, previous.sourceMetadata)
                else:
                    steps.append(CanonicalStep(1, f"{assertion_type.lower()} doğrulanır", [], target,
                                               locatorEvidence=[evidence] if evidence else [],
                                               assertions=[assertion], page={"alias": alias},
                                               sourceMetadata={"line": line_number, "raw": original}))
                continue
            match = ACTION.fullmatch(line)
            if match:
                expression, method, raw_args = match.groups()
                target, evidence, alias = _locator(expression, line_number, aliases)
                kind = {"click": "CLICK", "fill": "FILL", "selectOption": "SELECT",
                        "check": "CHECK", "uncheck": "UNCHECK", "press": "PRESS"}[method]
                value = _literal(raw_args) if kind in {"FILL", "SELECT", "PRESS"} else None
                if value is None and raw_args.strip():
                    raise UnsupportedInputError(f"Line {line_number}: unsupported action arguments")
                action = CanonicalAction(kind, target, value if kind in {"FILL", "SELECT"} else None,
                                         value if kind == "PRESS" else None, pageAlias=alias,
                                         provenance={"sourcePath": source_path, "line": line_number, "method": method})
                steps.append(CanonicalStep(len(steps)+1, _description(kind, target, len(steps)+1), [action], target,
                                           {"value": value} if value is not None else {}, [evidence],
                                           page={"alias": alias}, sourceMetadata={"line": line_number, "raw": original}))
                continue
            raise UnsupportedInputError(f"Line {line_number}: unsupported Playwright statement")
        if not steps:
            raise UnsupportedInputError("Playwright recording contains no supported actions or assertions")
        return CanonicalAutomationModel(test_case_id, "PLAYWRIGHT_RECORDING", steps,
                                        {"sourcePath": source_path, "parser": "known-patterns-v1"})
