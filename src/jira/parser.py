import csv
import io
import re
from pathlib import Path

from .models import Step, TestCase

KEY = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+")
ALIASES = {
    "action": "action", "step": "action", "teststep": "action", "teststeps": "action",
    "data": "data", "testdata": "data", "input": "data",
    "expectedresult": "expected", "expectedresults": "expected", "expected": "expected",
    "jirakey": "key", "issuekey": "key", "key": "key",
    "stepnumber": "number", "stepno": "number", "stepid": "number",
}


def parse_csv(path: Path, jira_key: str | None = None) -> TestCase:
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("CSV is empty")
    # Header detection avoids Sniffer failures on quoted multiline actions.
    def header_score(delimiter):
        header = next(csv.reader(io.StringIO(text), delimiter=delimiter))
        return sum(re.sub(r"[^a-z0-9]", "", name.lower()) in ALIASES for name in header)
    delimiter = max((",", ";"), key=header_score)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter, strict=True)
    columns = {}
    for raw in reader.fieldnames or []:
        name = ALIASES.get(re.sub(r"[^a-z0-9]", "", raw.lower()))
        if name:
            if name in columns.values():
                raise ValueError(f"Duplicate normalized column: {name}")
            columns[raw] = name
    if "action" not in columns.values():
        raise ValueError("CSV needs an Action column (or Step/Test Step)")
    keys, steps = set(), []
    for line, row in enumerate(reader, 2):
        if None in row:
            raise ValueError(f"CSV row {line} has extra fields; check quoting/delimiter")
        values = {alias: (row.get(raw) or "").strip() for raw, alias in columns.items()}
        if not any(values.values()):
            continue
        if values.get("key"):
            keys.add(values["key"].upper())
        action = values.get("action", "")
        if not action:
            raise ValueError(f"CSV row {line} has no action")
        number = len(steps) + 1
        if values.get("number") and values["number"] != str(number):
            raise ValueError("Step numbers must be consecutive, starting at 1")
        steps.append(Step(number, action, values.get("data", ""), values.get("expected", "")))
    if len(keys) > 1:
        raise ValueError("Multiple Jira cases in one CSV are not supported")
    filename_key = KEY.search(path.stem.upper())
    key = (jira_key or next(iter(keys), "") or (filename_key.group() if filename_key else "")).upper()
    if not KEY.fullmatch(key):
        raise ValueError("Supply a Jira key using --jira-key, CSV Issue Key, or filename")
    if keys and keys != {key}:
        raise ValueError("Configured Jira key conflicts with CSV content")
    if not steps:
        raise ValueError("CSV contains no steps")
    return TestCase(key, steps)
