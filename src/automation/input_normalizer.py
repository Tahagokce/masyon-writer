"""Select an input adapter and export the source-neutral canonical model."""

from __future__ import annotations

import json
from pathlib import Path

from .canonical import CanonicalAutomationModel, UnsupportedInputError
from .masyon_adapter import MasyonInputAdapter
from .playwright_adapter import PlaywrightInputAdapter


def detect_input_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise UnsupportedInputError("Invalid JSON input") from exc
        if isinstance(payload, dict) and "modelVersion" in payload:
            if payload["modelVersion"] == 1:
                return "canonical"
            raise UnsupportedInputError("Unsupported canonical model version")
        if isinstance(payload, dict) and isinstance(payload.get("steps"), list) and (
                isinstance(payload.get("jiraKey"), str) or
                payload.get("sourceType") == "MASYON" or
                isinstance(payload.get("testCaseId"), str)):
            return "masyon"
        raise UnsupportedInputError("Ambiguous JSON input; specify a supported MASYON handoff")
    if suffix in {".ts", ".js", ".mjs"}:
        text = path.read_text(encoding="utf-8")
        if "page." in text or "expect(" in text:
            return "playwright"
        raise UnsupportedInputError("JavaScript file has no recognizable Playwright calls")
    raise UnsupportedInputError("Cannot safely auto-detect input type")


def normalize(path: Path, input_type: str | None = None,
              test_case_id: str | None = None) -> CanonicalAutomationModel:
    if not path.is_file():
        raise UnsupportedInputError("Input file does not exist")
    kind = (input_type or detect_input_type(path)).lower()
    if kind == "masyon":
        return MasyonInputAdapter().read(path)
    if kind == "playwright":
        return PlaywrightInputAdapter().read(path, test_case_id)
    if kind == "canonical":
        return CanonicalAutomationModel.from_dict(json.loads(path.read_text(encoding="utf-8")))
    raise UnsupportedInputError("Input type must be masyon, playwright, or canonical")


def export_canonical(model: CanonicalAutomationModel, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "canonical-automation.json"
    if destination.exists():
        raise FileExistsError(destination)
    destination.write_text(json.dumps(model.to_dict(), ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return destination
