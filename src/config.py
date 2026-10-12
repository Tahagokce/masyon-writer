import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    command: list[str]
    base_url: str
    secret_names: list[str]
    max_step_calls: int
    step_timeout: int
    tool_timeout: int
    gemini_model: str = "gemini-3.8-flash"

    @classmethod
    def from_env(cls):
        raw = os.environ.get("PLAYWRIGHT_MCP_COMMAND")
        command = json.loads(raw) if raw else ["node", str(ROOT / "bridge/server.mjs")]
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise ValueError("PLAYWRIGHT_MCP_COMMAND must be a JSON argv array")
        names = [x.strip() for x in os.environ.get("SECRET_ENV_NAMES", "WBS_USERNAME,WBS_PASSWORD").split(",") if x.strip()]
        import re
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name == "GEMINI_API_KEY" for name in names):
            raise ValueError("SECRET_ENV_NAMES must contain browser credential variable names only")
        numbers = [int(os.environ.get(k, d)) for k, d in (
            ("MAX_STEP_CALLS", "30"), ("STEP_TIMEOUT_SECONDS", "300"), ("TOOL_TIMEOUT_SECONDS", "60"))]
        if any(n < 1 for n in numbers):
            raise ValueError("Call limits and timeouts must be positive")
        model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
        if not model or not re.fullmatch(r"[a-zA-Z0-9._/-]+", model):
            raise ValueError("GEMINI_MODEL must be a nonempty model identifier")
        return cls(command, os.environ.get("WBS_BASE_URL", ""), names, *numbers, model)
