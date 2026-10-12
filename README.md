# Jira → Gemini → fork-backed Playwright MCP

Phase 1 implementation for sequential Jira CSV execution, browser verification,
action-code recording and execution reports. Python orchestrates the official
Google GenAI SDK (model from `GEMINI_MODEL`, default `gemini-3.8-flash`) and official MCP Python client. A small Node
MCP bridge loads browser tools and selector generation directly from your built
Playwright fork. No standard Playwright package is installed by this project.

**Acceptance status (2026-10-10):** the custom ID-first fork, local browser/MCP
recording and replay pass. The real WBT-10891 run reached Gemini successfully but
stopped on application DNS resolution at step 1. Live step-5 acceptance and token
savings cannot be claimed. See [current results](docs/OPTIMIZATION_RESULTS.md) and
[original validation](docs/TEST_RESULTS.md).
The example CSV is a synthetic fixture, not a real Jira export.

## Local setup

Requirements: Python 3.11+, Node 20+ (tested with Node 24), a built custom fork
containing the current MCP/core APIs, and its installed Chromium browser (or an
explicit compatible Chrome executable).

```sh
cd /Users/sahih/Downloads/masyon
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install --no-deps -e .
npm ci --ignore-scripts
```

The verified upstream API baseline is `@playwright/mcp@0.0.83` /
`playwright-core@1.64.0-alpha-1790635538000`. Your fork needs the equivalent built
`lib/coreBundle.js`, `tools.createConnection`, `tools.parseResponse`, and
`tools.Tab.forPage` APIs. Follow your fork's build instructions. An older fork
requires bringing these MCP changes into the fork and rebuilding. This project
will not substitute an upstream library or modify your fork automatically.

Set environment variables using your shell or secret manager. Do not put real
values in source files or command arguments. `.env.example` documents all options;
the runner does not automatically read `.env` files.

```sh
export PLAYWRIGHT_FORK_PATH=/absolute/path/to/your/built-playwright-fork
export GEMINI_MODEL=gemini-3.8-flash
export WBS_BASE_URL=https://your-test-application.example
# Set GEMINI_API_KEY, WBS_USERNAME and WBS_PASSWORD securely in the environment.
# Optional, when not using the fork's installed Chromium:
export PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

# Check real stdio MCP startup and ID-first generation without calling Gemini.
.venv/bin/python check_fork.py

# Execute a real Jira CSV. No interactive input is needed during execution.
.venv/bin/python main.py /absolute/path/WBT-10891.csv
```

Required: `GEMINI_API_KEY`, `PLAYWRIGHT_FORK_PATH`; application credentials for
login steps. `WBS_BASE_URL` is needed only when the URL is not in the case.
`SECRET_ENV_NAMES` defaults to `WBS_USERNAME,WBS_PASSWORD`; add any additional
browser secrets there. Only names are sent to Gemini. Never add `GEMINI_API_KEY`
to browser secret names.

`GEMINI_MODEL` is read by configuration and passed to every SDK request. Only the
configuration default is fixed; the request logic contains no model name. Startup
logs `[CONFIG] Gemini model: ...`. Existing `.env` files are not modified or
automatically loaded. Explicitly export the values or use your normal secret loader.

`PLAYWRIGHT_MCP_COMMAND` optionally overrides the default
`["node", "/absolute/path/to/this/project/bridge/server.mjs"]` as a JSON argv
array, without shell evaluation. It must start this fork-aware bridge or a
compatible implementation of its control tools. Bare `npx @playwright/mcp` is
rejected. `HEADLESS=false` opens a visible browser. Call and timeout budgets are
listed in `.env.example`.

Exit codes: **0 SUCCESS**, **1 FAILED**, **2 BLOCKED**. Missing prerequisites,
ambiguous intent, budget exhaustion and uncertain outcomes are BLOCKED. A failed
browser assertion is FAILED. No later Jira step executes after failure/blocking.

## CSV and outputs

UTF-8 (with optional BOM), comma or semicolon, quoted multiline fields. Supported
column aliases include Action/Test Step, Data/Test Data, Expected Result/Expected
Results, Issue Key/Jira Key, and optional Step Number. One case per file. Jira key
priority is `--jira-key`, CSV content, then filename; conflicting CSV keys fail.

```sh
.venv/bin/python main.py examples/DEMO-1.csv --normalize-only
.venv/bin/python main.py /path/case.csv --jira-key WBT-10891 --output-dir outputs/run-02
```

Each run creates:

```text
outputs/WBT-10891/
  normalized-test-case.json
  recording.spec.ts
  execution.json
  run.log
```

An existing case directory is never overwritten. Choose a new `--output-dir` for
reruns. Reports include attempted steps, unexecuted step numbers, generated values,
page/opener relationships, fork path/version/hash and ID-first probe results,
recording completeness, Gemini request/token counts, browser-facing tool counts,
and underlying MCP calls. Metrics also include `snapshotCount`,
`snapshotFallbackCount`, averages per attempted step, and per-step deltas.
`browserToolCalls` counts individual operations in batches; `mcpToolCalls` includes
preflight and internal context/locator calls. `contextCharactersSent` measures
serialized conversation size, not tokens, and excludes system/tool definitions.
Set `LOG_LEVEL=DEBUG` for browser state, snapshot version, and full untrimmed
MCP snapshot blocks in `run.log`; live form values and secrets are redacted.
Set `LOG_LOCATORS=true` to log each action's actual locator from the fork-generated
Playwright code. Its default is `false`; fill values are always masked in action
logs. These runtime lines are kept out of the Gemini tool conversation and the
generated `recording.spec.ts`.
Failure recordings preserve available action code and
end in an explicit failure. When no recording was obtained, the `.spec.ts` is an
explicitly failing diagnostic marker, never a claimed successful recording.

Use the **fork's Playwright test distribution** to replay the generated spec.
Its test project must resolve `@playwright/test` and `playwright-core` to your
fork; do not install unqualified npm Playwright as a shortcut. Replay requires
the same credential environment variables. The application may need resetting
before replay because recorded values and actions are reused exactly.

## How execution and recording work

See the pre-implementation inspection and [implementation plan](docs/IMPLEMENTATION_PLAN.md).

* `src/jira`: CSV normalization and immutable step models.
* `src/gemini`: official SDK requests, bounded function calling and system rules.
* `src/mcp`: actual child-process stdio transport and isolated environment.
* `src/runner`: sequential step orchestration, verification gates and checkpoints.
* `src/state`, `src/security`, `src/recording`: reusable values, redaction and export.
* `bridge`: fork-only loading, real upstream MCP server, page events and codegen capture.

Every Jira step first calls the dedicated Gemini `submit_semantic_plan` function.
The runner validates its `actions` and `expectedAssertions`, then invokes
`browser_semantic_plan` directly. Ordinary steps cannot choose a snapshot as
their first browser operation. Gemini supplies only action type, a semantic target
such as `{ "name": "Save", "roleHint": "button" }`, and an optional value or
assertion. `bridge/ui-inventory.mjs` scans the whole active
page, including sibling tabs, and collects rendered names, roles, labels and
locator attributes. `bridge/locator-resolver.mjs` picks a unique semantic match,
then tries a stable DOM ID, test ID, role and accessible name, label, exact text,
placeholder, and finally a checked structural CSS path. Dynamic framework IDs are
skipped. `bridge/semantic-executor.mjs` performs the action on the fork's real
Playwright page and records the exact locator it executed. The bridge automatically
selects new pages and inventories them after mutations.

Ambiguous or unresolved targets return `AMBIGUOUS` or `NO_MATCH` with a compact
candidate list; only then does the runner open the fallback model conversation.
The executor never guesses one. Semantic assertions produce the same
evidence IDs used by the completion gate. `browser_snapshot`, snapshot refs,
`browser_batch` and legacy `browser_assert` remain available for compatibility,
unexpected UI state and recovery. The legacy click/type/select/assert tools also
accept `semanticTarget` during migration; their existing ref inputs still work.

The preflight performs real MCP input/button actions on a local fixture using
accessibility references. Both generated locators must prefer the fixture's IDs.
This tests the loaded code's behavior, not merely a configured pathname.

Recording starts before step 1 and stops in a `finally` block. It captures code
generated by the fork for legacy MCP operations and the exact fork-Playwright
locators executed by semantic actions. The GUI recorder is intended
for manual gestures; `--save-session` is a transcript, not a runnable spec. The
bridge uses the fork's response parser for code-bearing MCP responses. In this version,
JSON mode implies raw mode and excludes generated code, so JSON is used only for
explicit structured snapshots. Model-written guesses never become action code.

The bridge observes browser page/dialog events, maintains stable aliases, selects
new pages in MCP, and inserts listeners before the action that caused a popup or
dialog. TypeScript AST rewriting preserves string literals while rebinding page
references. The final TypeScript receives a syntax check; the supplied integration
harness can additionally replay it in a real browser.

`browser_assert` is a constrained trusted tool. It obtains a fork-generated locator
through MCP, then performs a fixed assertion loop through upstream MCP's code tool.
Its run log records the request, the resolved locator, and, on failure, the
available visibility, text, and selected state with form values redacted.
It checks actual visibility, text, value, checked/enabled state or exact URL. Gemini
cannot submit arbitrary JavaScript. Assertions produce evidence IDs. Completion
requires successful operations and relevant evidence produced after the latest
mutation. The model still judges whether its assertions cover the Jira requirement;
the mechanical gate cannot prove natural-language business semantics.

## Context and secrets

Every Jira step creates a fresh `StepContext`. Completed steps leave only their
numbers, generated values, known pages/opener/closed state, active page/current URL
and a summary of at most 400 characters. No previous step's detailed history or
snapshot is sent. Within a step, only the latest complete model/tool exchange is
kept, preserving thought signatures verbatim. Snapshot data is removed from tool
history and stored once as `latestInspection`; a new snapshot replaces it and a
mutation clears it. At most eight compact operation receipts are retained.

Snapshots prefer the last visible `[role="dialog"]`, then visible Ant Design and
generic modal containers, then the page. Explicit scopes are checked for unique
visible matches. A scoped failure falls back to a whole-page snapshot and logs
`[SNAPSHOT] Falling back to whole-page snapshot`. A still-unavailable inspection
returns a recoverable error if the browser/page is usable. It does not count as
assertion evidence or fail a business step by itself. Use `scope=page` to inspect
outside a modal. Default depth is 5; scoped/deeper requests remain available.
Snapshot trees are trimmed to about 4,000 characters as valid JSON with a visible
truncation flag; text, references and relevant element state are preserved.

The runner invokes `browser_semantic_plan` for normal UI work; its semantic actions
are resolved against a fresh active-page inventory without asking Gemini for selectors.
`browser_batch` remains available for 1–5 legacy ref actions on one stable screen.
Native multi-field `browser_fill_form` uses the same per-field guard.
Every action still runs and records through MCP. Navigation/reload, new pages,
modal/dialog changes, visible text/control changes and validation changes require
a model checkpoint. Click, key and submit operations always end a batch, even
before asynchronous changes appear. Remaining actions are explicitly unexecuted.
Multiple function calls from one model response obey the same checkpoints.
Oversized context stops at 30,000 characters; step deadlines and call budgets remain.

Browser fill tools receive environment variable **names**. MCP resolves them in
memory and generates `process.env['WBS_PASSWORD']!`, etc. Known secrets and common
encodings are scrubbed from browser results and persisted outputs. Code containing
secrets/redaction markers is rejected, rather than exported as a broken replay.
Logs contain tool names/statuses and generated values, not model transcripts,
arguments, snapshots or raw exception stacks. Failures include tool name, error
class, a redacted concise actual message, page alias and Jira step number.
Console persistence and incidental
MCP file output are disabled. No screenshot, trace, video or storage-state tools
are exposed. Gemini's API key is never forwarded to the browser child process.

Register all browser credentials with `SECRET_ENV_NAMES`. Pattern-based masking is
not a universal detector for arbitrary sensitive text rendered by an application.
Use a dedicated test environment/account and avoid sensitive business data in CSV
content intended for Gemini.

## Tests and current limitations

```sh
.venv/bin/python -m unittest discover -s tests -v
npm test
# Enable real modal/batch tests (otherwise these are explicitly skipped):
PLAYWRIGHT_FORK_PATH=/path/to/built/fork npm test
# Payload-size benchmark; this does not measure live Gemini tokens:
PYTHONPATH=. .venv/bin/python tests/measure_context.py

# Real fork + real browser/MCP integration; no Gemini call:
PLAYWRIGHT_FORK_PATH=/path/to/built/fork node tests/live_contract.mjs
# Optional replay in a test project resolving @playwright/test to the same fork:
# CONTRACT_OUTPUT=/path/to/fork-test-project
# CONTRACT_REPLAY_CLI=/path/to/fork/packages/playwright/cli.js
```

`tests/live_contract.mjs --upstream-contract` is explicitly a developer contract
test for standard upstream Playwright. It must demonstrate that production
preflight rejects that installation. It directly tests the journal separately;
it cannot make a production run bypass preflight and is not POC acceptance.

Known limits:

* The 2026-10-10 real application run stopped on DNS resolution. The five-step
  baseline cannot be compared with this one-request failure. Unit protocol test
  doubles are labeled as such and never used by the runner.
* Last-visible-dialog order uses DOM order; applications that stack dialogs in a
  different order can supply an explicit visible target. Scope races fall back.
* Batch guards are conservative. They inspect state after each completed operation;
  delayed changes can still occur after a checkpoint. Click/key/submit always stops.
* Aggressive snapshot trimming may require a narrower or deeper follow-up inspection.
* Fork adapter uses version-dependent MCP/private APIs. Unsupported builds stop.
* One isolated Chromium session and one recording per run. MFA/CAPTCHA or unclear
  business actions require BLOCKED. No file upload/download workflows in Phase 1.
* A late popup is attributed to the most recent mutating operation. Spontaneous
  popups without an originating action invalidate recording completeness.
* Hidden checks need a resolvable element/selector; fully removed targets whose
  prior reference cannot be resolved cannot be verified by that assertion.
* Failed calls can have partial browser effects. No automatic action retries;
  reports and partial recordings do not imply transactional rollback.
* A process kill, unavailable bridge or failed code secret scan can prevent
  recording export; checkpoints preserve the report and flag unavailable recording.
* The Phase 1 runner does not generate Java or Cucumber automation.

## Phase 2: multi-source writer

`masyon-writer` accepts MASYON JSON or a manual Playwright `.ts`/`.js` file.
Both adapters produce `CanonicalAutomationModel` before the existing repository
analysis and Java proposal pipeline. A canonical step has an ordered `actions`
list (so a Jira step can contain more than one action), target and data,
all locator evidence, assertions, page information, and source metadata.
Actions, assertions, and locators carry provenance. `PROVEN` means verified by
MASYON, `RECORDED` means present in a manual recording, and `INFERRED` means
derived from wording. The generator consumes the canonical model only.

```sh
python3 masyon_writer.py normalize \
  --input outputs/WBT-10891/automation/normalized-test-case.json \
  --output-dir outputs/WBT-10891/normalization

python3 masyon_writer.py normalize \
  --input examples/WBT-MANUAL-1.ts --test-case-id WBT-MANUAL-1 \
  --output-dir outputs/WBT-MANUAL-1/normalization
```

Each command writes `canonical-automation.json`. It can be passed back as
`--input` for generation. MASYON `execution.json` is supported when a sibling
`normalized-test-case.json` is present. Failed execution does not confer proven
locator confidence. The MASYON adapter keeps original Jira wording, Data,
expected results, semantic plans, locator evidence, page/popup data, and
execution metadata.
When a sibling `recording.spec.ts` exists, the model records its path and SHA-256
as an unassigned source artifact. The recording format does not identify which
Jira step owns each locator, so normalization does not attach its locators to a
step by position.

The Playwright adapter handles literal `locator('#id')`, data-testid CSS,
`getByRole`, `getByLabel`, `getByText`, `getByPlaceholder`, and `getByTestId`;
click, fill, selectOption, check, uncheck, press, goto, waitForTimeout, and
popup page aliases from `waitForEvent('popup')`; plus
visible, hidden, text, value, checked, selected, enabled, disabled, and URL
assertions. It rejects dynamic IDs and unsupported JavaScript with
`UNSUPPORTED_INPUT`. Variables, loops, conditionals, arbitrary JavaScript,
chained locators, and nonliteral arguments are outside this parser's scope.

For generation, set an external Java+Cucumber repository with
`AUTOMATION_REPO_PATH` or `--automation-repo`. The path must be absolute and may
be outside this project. The external repository is read-only; provable Java
changes are copies under `proposed-patch/`.

```sh
export AUTOMATION_REPO_PATH=/absolute/path/to/java-cucumber-repository
python3 masyon_writer.py generate \
  --input outputs/WBT-10891/normalization/canonical-automation.json \
  --output-dir outputs/WBT-10891/automation-proposal
```

The repository and recording arguments are optional for producing the feature.
The repository index includes step annotations, Page Objects, locators, helpers,
packages, DI, assertion and wait imports. The matcher reports exact,
parameterized, semantic, missing, and ambiguous bindings. Regex annotations are
matched as regex; overlapping matches are ambiguous. Java is proposed only for a
missing button click with one stable Page Object locator, one matching Page Object
helper, and one injected step field. Other missing behavior remains unresolved.
Validation scans for duplicate annotations and candidate undefined steps. With a
`pom.xml`, it copies the repository into a temporary directory and runs Maven
`test-compile` and `test` with Cucumber dry-run enabled. The external repository
is never modified. Use `--no-validate` to skip Maven. Existing output directories
are preserved, so choose a new `--output-dir` for another run.
# masyon-writer
