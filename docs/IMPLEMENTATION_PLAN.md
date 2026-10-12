# Implementation plan and inspected integration contract

Inspected 2026-10-07, before implementation. Workspace was empty. No custom fork,
CSV or credentials were supplied or found in the initial local search. Consequently
the user's fork cannot yet be certified; missing inputs are not replaced with npm
Playwright or simulated Gemini decisions.

## Findings

* npm `@playwright/mcp@0.0.83` pins `playwright` and `playwright-core` to
  `1.64.0-alpha-1790635538000`. Its index exports
  `require('playwright-core/lib/coreBundle').tools.createConnection`.
* Current MCP implementation lives in Playwright core. `createConnection(config,
  contextGetter)` permits supplying a browser context from the same built fork.
* `browser_start_recording` records user gestures; it is not a general recorder
  for programmatic MCP actions. `saveSession` writes a Markdown tool transcript,
  not a runnable spec, and includes arguments. Neither is our recording mechanism.
* MCP action responses contain a `Ran Playwright code` section emitted by
  Playwright's language generator. Its actions use `page` even when another tab
  is active. Blindly concatenating responses is incorrect for popups. Runtime
  validation found that JSON mode omits code in this release; the implementation
  uses the fork's response parser for actions and JSON only for snapshots.
* MCP secret lookup supports secret **names** as form values and emits environment
  expressions. This keeps raw login credentials away from Gemini.
* Some built-in verification tools check element existence instead of visibility.
  A trusted assertion tool therefore performs a bounded assertion polling loop
  via upstream MCP's `browser_run_code_unsafe`, with generated locators. The model
  cannot call that unrestricted code tool directly.

## Architecture and implementation sequence

1. Python dataclasses and standard-library CSV parser normalize one Jira case.
2. A Node stdio MCP bridge uses the official MCP SDK. It loads the configured
   built fork by absolute realpath, then connects to its real upstream MCP server
   via SDK linked transports. There is no npm Playwright dependency or fallback.
3. Before recording, an isolated local fixture checks MCP-generated button and
   input locators use stable IDs. Reject incompatible APIs or selector behavior.
4. Gemini 2.5 Flash receives only the current step, reusable generated values,
   compact browser state, and bounded tool exchanges. SDK automatic function
   calling is disabled; all calls execute sequentially through MCP.
5. Start an action-code recording before the first Jira step. Capture successful
   MCP code, rewrite only the `page` identifier using TypeScript's AST, and record
   live page events with opener relationships. Add listeners before the originating
   action in the emitted spec. Automatically select new pages in MCP.
6. Credentials are names, resolved only in the bridge. Mask known secret values
   and common encoded representations. No screenshots, traces, session logs or
   raw SDK transcripts. Disable MCP file outputs and console persistence.
7. Completion requires an action receipt and successful assertion evidence newer
   than the last mutation for any step with an expected result. The model must
   tie the assertions to that expected result; semantic adequacy still depends on
   the model. Missing intent becomes BLOCKED. Stop on the first non-completed step.
8. Save normalized JSON, actual action-code TypeScript, execution report and terse
   operational log. Record requests, tokens, tool counts and preflight provenance.
9. Unit tests cover parsing, secret handling and orchestration failure rules.
   Integration tests use a real browser/MCP; no test double qualifies as acceptance.

## Limits and acceptance

The fork must carry the current MCP/core APIs; an older fork needs the corresponding
upstream changes rebuilt into it. This bridge deliberately rejects unsupported
versions instead of editing/replacing the user's fork. Private integration points
are isolated in the bridge and covered by preflight. Recorded partial failures
are explicitly failing tests, not passing empty placeholders. Late asynchronous
popups are attached to the latest browser action; spontaneous pages without an
originating action invalidate recording. Arbitrary JS, uploads, downloads and
file-producing tools are not exposed to Gemini in Phase 1. Authentication requiring
human MFA/CAPTCHA is BLOCKED. Full POC acceptance is unverified until a real fork,
Gemini key, application and Jira case are provided and run.

Sources inspected:

* https://github.com/microsoft/playwright-mcp/blob/main/index.js
* https://github.com/microsoft/playwright-mcp/blob/main/package.json
* https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/mcp/index.ts
* https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/backend/response.ts
* https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/backend/codegen.ts
* https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/backend/context.ts
* https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/backend/recorder.ts
* https://ai.google.dev/gemini-api/docs/function-calling
