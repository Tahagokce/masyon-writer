import asyncio
import os
from pathlib import Path

from src.config import Config
from src.gemini.client import GeminiClient
from src.gemini.prompts import LOCAL_TOOLS
from src.jira.parser import parse_csv
from src.mcp.client import BrowserClient
from src.recording.service import Recording, unavailable_recording, write_json
from src.security.secrets import Secrets
from src.state.execution_state import ExecutionState
from .step_runner import run_step


async def run_case(csv_path: Path, output: Path, jira_key=None):
    config = Config.from_env()
    secrets = Secrets([*config.secret_names, "GEMINI_API_KEY"])
    case = parse_csv(csv_path, jira_key)
    folder = output / case.jiraKey
    # Never silently overwrite evidence from an earlier execution.
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_json(folder / "normalized-test-case.json", case.to_dict(), secrets)
    unavailable_recording(folder)
    state = ExecutionState()
    report = {"jiraKey": case.jiraKey, "runId": state.run_id, "status": "BLOCKED", "steps": [],
              "generatedValues": state.generated_values, "metrics": state.metrics,
              "recording": {"available": False}, "geminiModel":config.gemini_model}

    with open(folder / "run.log", "w", encoding="utf-8") as logfile:
        os.chmod(folder / "run.log", 0o600)

        def log(message):
            logfile.write(secrets.clean(message) + "\n")
            logfile.flush()

        def checkpoint():
            write_json(folder / "execution.json", report, secrets)

        checkpoint()
        log(f"[CONFIG] Gemini model: {config.gemini_model}")
        try:
            if not os.environ.get("PLAYWRIGHT_FORK_PATH"):
                raise ValueError("PLAYWRIGHT_FORK_PATH is required; standard Playwright fallback is forbidden")
            if not os.environ.get("GEMINI_API_KEY"):
                raise ValueError("GEMINI_API_KEY is required")
            async with BrowserClient(config) as browser:
                preflight = await browser.call("poc_preflight", {})
                report["preflight"] = preflight
                if preflight.get("isError") or not preflight.get("idFirstVerified"):
                    raise ValueError("Fork preflight failed: " + preflight.get("error", "ID-first code generation not verified"))
                log("[MCP] Fork identity and ID-first codegen preflight passed")
                recording = Recording(browser, folder, secrets)
                state.observe(await recording.start())
                ai = None
                try:
                    ai = GeminiClient(os.environ["GEMINI_API_KEY"],
                                      [t for t in browser.tools if not t["name"].startswith("poc_")] + LOCAL_TOOLS,
                                      state, config.gemini_model)
                    for step in case.steps:
                        log(f"[JIRA] Step {step.stepNumber}")
                        before = state.metrics.copy()
                        result = await run_step(step, ai, browser, state, config, secrets, log)
                        result["metrics"] = {key:state.metrics[key]-value for key,value in before.items()}
                        report["steps"].append(result)
                        log(f"[STEP {step.stepNumber}] {result['status']}")
                        checkpoint()
                        if result["status"] != "COMPLETED":
                            report["status"] = result["status"]
                            report["reason"] = result["reason"]
                            break
                    else:
                        report["status"] = "SUCCESS"
                except BaseException as exc:
                    report["status"] = "BLOCKED"
                    details = secrets.diagnostic(exc)
                    report["reason"] = f"{details['errorClass']}: {details['error']}"
                    report["errorDetails"] = details
                    raise
                finally:
                    try:
                        report["recording"] = await recording.stop(report["status"], report.get("reason", ""))
                        state.metrics["mcpToolCalls"] = report["recording"].get("mcpToolCalls", 0)
                        state.observe(report["recording"])
                        if not report["recording"].get("complete", False):
                            report["status"] = "BLOCKED"
                            report["reason"] = "Only a partial recording is available; see recording.spec.ts"
                    except Exception as exc:
                        report["status"] = "BLOCKED"
                        report["recordingError"] = secrets.diagnostic(exc)
                        report.setdefault("reason", "Recording finalization failed; no successful POC can be claimed")
                    if ai:
                        await ai.close()
        except (Exception, asyncio.CancelledError) as exc:
            report["status"] = "BLOCKED"
            details = secrets.diagnostic(exc)
            report.setdefault("reason", f"{details['errorClass']}: {details['error']}")
            report.setdefault("errorDetails", details)
            log("[RUN] " + report["reason"])
        finally:
            attempted = len(report["steps"])
            if report["status"] != "SUCCESS" and attempted < len(case.steps):
                if not report["steps"] or report["steps"][-1]["status"] == "COMPLETED":
                    report["steps"].append({"stepNumber": case.steps[attempted].stepNumber,
                        "status": "BLOCKED", "reason": report.get("reason", "Execution stopped"), "verification": ""})
            report["notRunSteps"] = [s.stepNumber for s in case.steps[len(report["steps"]):]]
            report["pages"] = state.pages
            count = max(1, len([s for s in report["steps"] if "metrics" in s]))
            state.metrics["averageGeminiRequestsPerStep"] = round(state.metrics["geminiRequests"] / count, 2)
            state.metrics["averageInputTokensPerStep"] = round(state.metrics["inputTokens"] / count, 2)
            report["compactState"] = state.compact()
            log("[METRICS] " + str(state.metrics))
            checkpoint()
    return report, folder
