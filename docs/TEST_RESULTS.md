# Actual validation results — 2026-10-07 (Europe/Istanbul)

## Passed

| Check | Actual result |
| --- | --- |
| Python unit tests | 11 passed |
| Node recorder/security tests | 6 passed |
| Python compilation / Node syntax checks | Passed |
| npm dependency audit | 0 vulnerabilities in project dependencies |
| Python dependency consistency | `pip check` passed |
| Official Python MCP client → stdio Node bridge → browser | Connected; returned real selector preflight result |
| Standard Playwright rejection | Preflight observed role-based selectors and rejected recording start |
| Real MCP local fixture | Login, menu/navigation, popup, new tab, switch/close, fill, key press, select and dialog passed |
| Browser assertions | 8 successful assertions; hidden-but-present element correctly rejected |
| Recording secret scan | Passed; synthetic credentials absent; environment expressions present |
| Generated spec replay | Passed in a second real headless Chrome session |
| Strict TypeScript | `tsc --noEmit --strict --target ES2022 --module NodeNext --moduleResolution NodeNext --skipLibCheck --types node recording.spec.ts` passed |
| Missing prerequisites | CLI returned exit 2 / BLOCKED, wrote all four artifacts, marked remaining steps unexecuted |

Browser integration used a local HTTP fixture and a separately downloaded upstream
`playwright-core@1.64.0-alpha-1790635538000`, loaded from `/tmp`. Replay used the
matching upstream test runner under `/tmp/masyon-validation`. Neither package is a
runtime dependency of this POC. Chrome executable:
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.

The upstream contract harness explicitly asserted that the production ID-first
gate rejected upstream Playwright. It then tested the recording component directly
in isolation. This test-only path is not exposed through the production MCP server.

Evidence:

* [Machine-readable upstream contract result](validation/upstream-contract.json)
* [Actual captured and replayed TypeScript](validation/upstream-recording.spec.ts)

The recorded localhost port is ephemeral; this archived spec is evidence, not an
application test. Regenerate/replay it with `tests/live_contract.mjs`. Fresh random
fixture credentials existed only in the test process environment and are not saved.

## Not verified / acceptance still BLOCKED

* User's custom fork: no path/repository supplied; cannot inspect its patch or
  demonstrate that its recorded locators prefer IDs.
* Real Jira CSV: not supplied; `examples/DEMO-1.csv` is a synthetic four-step sample.
* Real Gemini request/decision loop: no `GEMINI_API_KEY` available. Python unit
  protocol responses are explicitly test doubles, never an acceptance run.
* Target application login/business results: application URL and credentials absent.

Therefore the requested real POC acceptance criteria are **not claimed successful**.
Once inputs are configured, run `check_fork.py` and `main.py` as described in README.
No screenshots, Java/Cucumber files, commits or pull requests were produced.
