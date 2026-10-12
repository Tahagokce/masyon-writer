import json
import os
from pathlib import Path


def write_text(path: Path, text: str):
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        os.chmod(temp, 0o600)
        handle.write(text)
    temp.replace(path)


def write_json(path: Path, value, secrets):
    write_text(path, json.dumps(secrets.object(value), ensure_ascii=False, indent=2) + "\n")


class Recording:
    def __init__(self, browser, folder, secrets):
        self.browser, self.folder, self.secrets = browser, folder, secrets

    async def start(self):
        result = await self.browser.call("poc_record_start", {})
        if result.get("isError"):
            raise RuntimeError("Cannot start verified MCP action recording")
        return result

    async def stop(self, status, reason):
        result = await self.browser.call("poc_record_stop", {"status": status, "reason": self.secrets.clean(reason)})
        if result.get("isError") or not result.get("code"):
            raise RuntimeError("MCP action recording could not be finalized")
        if self.secrets.contains(result["code"]) or '[REDACTED]' in result["code"] or '<secret>' in result["code"]:
            raise RuntimeError("Secret detected in recorded code; export refused")
        write_text(self.folder / "recording.spec.ts", result["code"])
        return {k: v for k, v in result.items() if k != "code"}


def unavailable_recording(folder: Path):
    write_text(folder / "recording.spec.ts", "import { test } from '@playwright/test';\n"
               "// No browser recording was available. This is not a successful test.\n"
               "test('recording unavailable', async () => { throw new Error('POC BLOCKED: recording unavailable'); });\n")
