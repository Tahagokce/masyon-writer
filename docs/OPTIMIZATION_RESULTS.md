# Executor optimization validation — 2026-10-10

The modal failure is covered by live tests against the mandatory custom ID-first fork. The real WBT-10891 run could not reach the application: navigation failed with `net::ERR_NAME_NOT_RESOLVED` at step 1. The user confirmed that the corporate network/VPN is unavailable. Passing step 5 and reducing real Gemini input tokens remain unverified.

## Changes

| Files | Change |
| --- | --- |
| `bridge/snapshot.mjs`, `bridge/runtime.mjs` | Resolve one visible dialog before invoking strict snapshot APIs. Prefer role dialog, then Ant containers, then generic containers. Choose the last visible candidate; fall back to the whole page with a logged notice. Keep usable-browser inspection failures recoverable. Bound snapshots and preserve useful accessibility fields. |
| `bridge/batch.mjs`, `bridge/runtime.mjs` | Execute up to five deterministic actions through the existing MCP/recording path. Inspect screen changes between operations; stop at transitions, validation changes, errors, clicks and key presses. Apply the same guard between fields of native bulk fill. |
| `src/gemini/context.py`, `src/runner/step_runner.py`, `src/state/execution_state.py`, `src/gemini/prompts.py` | Start fresh detailed context for each Jira step. Carry compact generated values, completed step numbers and page aliases/opener/URL/state. Keep one current snapshot and one model/tool exchange, preserving thought signatures. Invalidate snapshots after mutations or failed inspection. Keep browser evidence mandatory for completion. |
| `.env.example`, `src/config.py`, `src/gemini/client.py` | Read `GEMINI_MODEL`, defaulting to `gemini-3.8-flash`. Send only the configured model to the SDK. Measure serialized context size alongside API token usage. |
| `bridge/security.mjs`, `bridge/server.mjs`, `src/security/secrets.py` | Preserve concise actual errors and class/tool/page context, redact credentials, sensitive URL parameters and authentication fields. Add Jira step details in runner reports. |
| `src/runner/test_case_runner.py`, `src/recording/service.py`, `main.py` | Log selected model, collect per-step and overall metrics, preserve recording metrics, and emit Turkish text as UTF-8 JSON. |
| `tests/test_optimization.py`, `tests/snapshot.test.mjs`, `tests/modal.live.test.mjs`, `tests/measure_context.py`, `package.json` | Add regression coverage, live modal/batch cases and a repeatable context-size measurement; include new Node tests in the suite. |
| `README.md`, this report, `docs/validation/optimization-metrics.json`, `docs/validation/custom-fork-contract-20261010.json` | Document configuration, behavior, measured results and limits. |

The original broad union matched both the outer `nz-modal-container[role=dialog]` and inner `.ant-modal[role=document]`. Passing that union directly into a strict locator operation caused the failure. Resolution now checks visible candidates and selects one before requesting a snapshot; caller-supplied ambiguous targets safely fall back too.

## Validation

- Python: **20 tests passed**, including existing tests plus configured/default model, context replacement, compact generated/page state, UTF-8 output, redaction, recoverable inspection, transition checkpoints and assertion failures.
- Node: **18 tests passed, zero skipped**, including **10 live custom-fork tests**. Cases cover one dialog, nested modal nodes, multiple dialogs, Ant/generic containers, no dialog, explicit page inspection, ambiguous-union fallback, injected strict-mode failure, safe batches, validation checkpoints and native bulk-fill checkpoints.
- Existing custom-fork contract: **passed**. ID-first preflight produced `#poc-stable-button` and `#poc-stable-input` locators; eight assertions, popup aliases, secret scan, hidden-element rejection and recording syntax passed. The generated recording replayed successfully through the custom fork's test runner.
- Python compilation and `git diff --check`: passed.

Live tests used `/Users/sahih/project/playwright-id-first` (core `1.65.0-next`) with explicitly configured installed Google Chrome because the expected headless-shell binary was absent. No standard Playwright fallback was used. The fork fingerprint and contract results are saved in [custom-fork-contract-20261010.json](validation/custom-fork-contract-20261010.json).

Reproduce the main suites from the project root:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
PLAYWRIGHT_FORK_PATH=/Users/sahih/project/playwright-id-first PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm test
PYTHONPATH=. .venv/bin/python tests/measure_context.py
```

Without `PLAYWRIGHT_FORK_PATH`, the live Node tests explicitly skip; that was not the configuration used for the results above.

## Measurements

| Metric | User-provided previous run | New real run |
| --- | ---: | ---: |
| Progress | Reached step 5 | Failed navigation at step 1 |
| Gemini requests | 37 | 1 |
| Input tokens | 169,981 | 2,963 |
| Output tokens | 1,648 | 26 |
| Thought tokens | 2,510 | 99 |
| Browser tool calls | 33 | 1 |
| MCP tool calls | 45 | 5 |
| Snapshots / snapshot fallbacks | Not supplied | 0 / 0 |

**These real runs are not comparable.** The new run stopped before login, so its lower totals do not demonstrate live token savings. Its model was `gemini-3.8-flash`; average requests/input tokens per attempted step were 1 / 2,963. Full local output is under `outputs/optimization-20261010-01/WBT-10891/`.

A controlled replay of **12 model-context payloads** measured **127,397 → 49,044 serialized characters (61.5% reduction)** using the previous four-exchange history policy versus the new context policy. This measures context characters only, excludes system instructions and tool schemas, and makes no claim about actual Gemini token savings or fewer live model calls. Run `tests/measure_context.py` to reproduce it. Structured measurements are saved in [optimization-metrics.json](validation/optimization-metrics.json).

## Remaining limits

- Real WBT-10891 step 5, subsequent steps and comparable token savings require access to the application network.
- Last-visible dialog selection uses DOM order, which is an approximation of opening order. Explicit target and page scopes remain available.
- Batching is intentionally conservative. Screen checks catch observed changes between actions but cannot guarantee that an arbitrarily delayed asynchronous update has already arrived.
- Large snapshots are marked as trimmed; additional targeted inspection may be needed. Existing per-step call/time limits still bound repeated recoverable failures.
- Assertions, page aliases, recording strategy and mandatory ID-first preflight remain in place. No Java/Cucumber generation, Jira API, RAG, CI/CD or Git automation was added.
