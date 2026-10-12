"""Read-only, conservative index of a Java+Cucumber repository."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


ANNOTATION = re.compile(r'@(Given|When|Then|And|But)\s*\(\s*"((?:\\.|[^"\\])*)"\s*\)', re.DOTALL)
PACKAGE = re.compile(r"\bpackage\s+([\w.]+)\s*;")
CLASS = re.compile(r"\bclass\s+(\w+)")
METHOD = re.compile(r"\b(public|protected|private)\s+(?:static\s+)?(?:[\w<>?,.\[\]]+\s+)+(\w+)\s*\(([^;{}]*)\)\s*(?:throws\s+[\w,. ]+)?\{")
FIELD = re.compile(r"\b(?:private|protected|public)\s+(?:final\s+)?(\w+)\s+(\w+)\s*;")
LOCATOR = re.compile(r'\bBy\.(id|cssSelector|xpath|name|className)\s*\(\s*"((?:\\.|[^"\\])*)"\s*\)')
FIND_BY = re.compile(r'@FindBy\s*\(\s*(id|css|xpath|name|className)\s*=\s*"((?:\\.|[^"\\])*)"\s*\)')
PLAYWRIGHT_LOCATOR = re.compile(r'\b(getByTestId|getByLabel|locator)\s*\(\s*"((?:\\.|[^"\\])*)"\s*\)')
SKIP = {".git", "target", "build", ".gradle", "node_modules", ".idea"}
COMMENTS = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|//[^\n]*|/\*[\s\S]*?\*/''')


def _strip_comments(source: str) -> str:
    return COMMENTS.sub(lambda m: re.sub(r"[^\n]", " ", m.group())
                        if m.group().startswith(("//", "/*")) else m.group(), source)


def java_string(raw: str) -> str:
    try:
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw.replace(r'\"', '"')


def stable_id(value: str) -> bool:
    return bool(value and re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]*", value)
                and not re.search(r"(?:ember|react-select|mat-input|uuid)[-_]?\d+|\d{5,}|[0-9a-f]{8}-[0-9a-f]{4}", value, re.I))


def _locator_kind(kind: str, value: str) -> tuple[str, str, bool]:
    if kind in {"css", "cssSelector", "locator"}:
        if value.startswith("#") and stable_id(value[1:]):
            return "id", value[1:], True
        match = re.fullmatch(r'''\[data-testid\s*=\s*['"]([^'"]+)['"]\]''', value)
        if match and stable_id(match.group(1)):
            return "testid", match.group(1), True
        return kind, value, False
    if kind == "getByTestId":
        return "testid", value, stable_id(value)
    if kind == "getByLabel":
        return "label", value, bool(value.strip())
    return kind, value, stable_id(value) if kind == "id" else kind not in {"xpath"}


@dataclass(frozen=True)
class Binding:
    expression: str
    keyword: str
    source: str
    package: str
    className: str
    method: str
    expressionType: str


def expression_type(value: str) -> str:
    if value.startswith("^") or value.endswith("$"):
        return "REGEX"
    if re.search(r"\{[^}]*\}", value):
        return "CUCUMBER_EXPRESSION"
    return "LITERAL"


def _method_after(source: str, end: int) -> str:
    match = METHOD.search(source, end)
    return match.group(2) if match else "UNKNOWN"


def index_repository(repository: Path) -> dict:
    repository = repository.expanduser()
    if not repository.is_absolute() or not repository.is_dir():
        raise ValueError("AUTOMATION_REPO_PATH must be an existing absolute directory")
    repository = repository.resolve()
    bindings, pages, locators, helpers, step_classes = [], [], [], [], []
    packages: set[str] = set()
    imports: set[str] = set()
    for path in sorted(repository.rglob("*.java")):
        if any(part in SKIP for part in path.relative_to(repository).parts):
            continue
        source = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        relative = str(path.relative_to(repository))
        package_match, class_match = PACKAGE.search(source), CLASS.search(source)
        package = package_match.group(1) if package_match else ""
        class_name = class_match.group(1) if class_match else path.stem
        if package:
            packages.add(package)
        imports.update(re.findall(r"(?m)^\s*import\s+([\w.*]+)\s*;", source))
        file_bindings = []
        for match in ANNOTATION.finditer(source):
            expression = java_string(match.group(2))
            item = Binding(expression, match.group(1), relative, package, class_name,
                           _method_after(source, match.end()), expression_type(expression))
            bindings.append(asdict(item))
            file_bindings.append(item)
        is_page = bool(re.search(r"Page(?:Object)?$", class_name) or "@FindBy" in source or
                       "PageFactory.initElements" in source)
        if is_page:
            pages.append({"className": class_name, "package": package, "source": relative})
        for pattern, origin in ((LOCATOR, "By"), (FIND_BY, "FindBy"),
                                (PLAYWRIGHT_LOCATOR, "JavaPlaywright")):
            for match in pattern.finditer(source):
                kind, value = match.group(1), java_string(match.group(2))
                kind, value, stable = _locator_kind(kind, value)
                tail = source[match.end():match.end()+120]
                before = source[max(0, match.start()-100):match.start()]
                name_match = re.search(r"\b(\w+)\s*(?:=\s*)?$", before)
                after_match = re.search(r"\b(\w+)\s*;", tail)
                field_name = (after_match.group(1) if origin == "FindBy" and after_match else
                              name_match.group(1) if name_match else "")
                locators.append({"source": relative, "pageClass": class_name if is_page else None,
                                 "field": field_name, "kind": kind, "value": value,
                                 "stable": stable,
                                 "origin": origin})
        for match in METHOD.finditer(source):
            if match.group(2) != class_name:
                body_end = source.find("}", match.end())
                body = source[match.end():body_end if body_end >= 0 else match.end()]
                helpers.append({"source": relative, "className": class_name,
                                "package": package, "name": match.group(2),
                                "pageObject": is_page, "visibility": match.group(1),
                                "zeroArguments": not match.group(3).strip(),
                                "references": sorted(set(re.findall(r"\b\w+\b", body)))})
        if file_bindings:
            fields = []
            for typ, name in FIELD.findall(source):
                constructor = re.search(r"\b" + re.escape(class_name) + r"\s*\([^)]*\b" +
                                        re.escape(typ) + r"\s+" + re.escape(name) + r"\b[^)]*\)", source)
                assigned = re.search(r"\bthis\." + re.escape(name) + r"\s*=\s*" + re.escape(name) + r"\s*;", source)
                annotation = re.search(r"@(?:Inject|Autowired)\s+(?:(?:private|protected|public|final)\s+)*" +
                                       re.escape(typ) + r"\s+" + re.escape(name), source)
                fields.append({"type": typ, "name": name,
                               "injected": bool((constructor and assigned) or annotation)})
            step_classes.append({"source": relative, "package": package,
                                 "className": class_name, "fields": fields})
    return {"repository": str(repository), "bindings": bindings, "pageObjects": pages,
            "locators": locators, "helpers": helpers, "stepClasses": step_classes,
            "conventions": {"packages": sorted(packages),
                            "dependencyInjection": sorted(x for x in imports if re.search(r"inject|pico|spring", x, re.I)),
                            "assertions": sorted(x for x in imports if re.search(r"assert|hamcrest", x, re.I)),
                            "waits": sorted(x for x in imports if re.search(r"wait|awaitility", x, re.I))}}


def expression_pattern(expression: str) -> re.Pattern[str] | None:
    kind = expression_type(expression)
    if kind == "REGEX":
        try:
            return re.compile(expression, re.I)
        except re.error:
            return None
    if kind == "LITERAL":
        return re.compile("^" + re.escape(expression) + "$", re.I)
    parts, cursor = [], 0
    for match in re.finditer(r"\{([^}]*)\}", expression):
        replacement = {"string": r'''(?:"[^"\n]*"|'[^'\n]*')''',
                       "int": r"-?\d+", "long": r"-?\d+",
                       "float": r"-?\d+(?:\.\d+)?", "double": r"-?\d+(?:\.\d+)?",
                       "bigdecimal": r"-?\d+(?:\.\d+)?", "word": r"\S+",
                       "": r".+?"}.get(match.group(1).casefold())
        if replacement is None:
            return None
        parts.append(re.escape(expression[cursor:match.start()]))
        parts.append(replacement)
        cursor = match.end()
    if re.search(r"\{[^}]*\}", expression[cursor:]):
        return None
    parts.append(re.escape(expression[cursor:]))
    return re.compile("^(?:" + "".join(parts) + ")$", re.I)
