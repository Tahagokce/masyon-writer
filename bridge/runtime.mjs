import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { Journal } from './recording.mjs';
import { SecretGuard } from './security.mjs';
import { inspectSnapshot, compactSnapshot, debugSnapshot } from './snapshot.mjs';
import { executeBatch, screenStamp, BATCH_TOOLS } from './batch.mjs';
import { staleRef, descriptor, hasUsableDescriptor, rememberSnapshotRefs, resolveAssertionTarget } from './assertion-recovery.mjs';
import { actionLogLines } from './action-logging.mjs';
import { assertionRequest, assertionResolved, assertionFailure } from './assertion-logging.mjs';
import { uiInventory } from './ui-inventory.mjs';
import { executeSemanticAction, executeSemanticPlan } from './semantic-executor.mjs';

const require = createRequire(import.meta.url);
const ALLOWED = new Set(['browser_navigate', 'browser_navigate_back', 'browser_navigate_forward',
  'browser_reload', 'browser_click', 'browser_type', 'browser_fill_form', 'browser_press_key',
  'browser_select_option', 'browser_hover', 'browser_drag', 'browser_snapshot', 'browser_wait_for',
  'browser_handle_dialog']);
const READ = new Set(['browser_snapshot', 'browser_wait_for']);
const str = { type: 'string' };
const schema = (name, description, properties = {}, required = []) => ({name, description,
  inputSchema: {type: 'object', properties, required, additionalProperties: false}});
const semanticTarget = {type:'object',properties:{name:str,roleHint:str,label:str,text:str,placeholder:str},
  additionalProperties:false};
const semanticAction = {type:'object',properties:{
  type:{type:'string',enum:['click','fill','select','check','uncheck','press','navigate','assert']},
  target:semanticTarget,value:str,key:str,url:str,
  kind:{type:'string',enum:['visible','hidden','text','value','enabled','disabled','checked','unchecked','selected','unselected','url']},
  expected:str},required:['type'],additionalProperties:false};
export const CONTROL_TOOLS = [
  schema('browser_semantic_plan', 'Execute a semantic action plan on the active page. Resolve elements from a whole-page UI inventory; stop for ambiguity or failure. Include expected-result assertions as type=assert.',
    {actions:{type:'array',minItems:1,maxItems:10,items:semanticAction}},['actions']),
  schema('browser_semantic_action', 'Execute one semantic browser action using the current UI inventory.',
    {action:semanticAction},['action']),
  schema('browser_batch', 'Execute up to five known actions on the same stable screen. Stops after click/key/submit or any UI transition. Inspect and replan remaining actions at the next checkpoint.',
    {actions:{type:'array', minItems:1, maxItems:5, items:{type:'object', properties:{
      tool:{type:'string', enum:BATCH_TOOLS}, arguments:{type:'object', properties:{
        target:str, text:str, element:str, key:str, submit:{type:'boolean'},
        values:{type:'array',items:str}, fields:{type:'array',items:{type:'object',properties:{
          name:str,type:str,target:str,value:str},required:['name','type','target','value']}}
      }}},required:['tool','arguments']}}}, ['actions']),
  schema('poc_preflight', 'Verify fork provenance and actual ID-first MCP code generation.'),
  schema('poc_record_start', 'Start actual MCP action recording after successful fork preflight.'),
  schema('poc_step_start', 'Set the active page and snapshot budget for one Jira step.',
    {stepNumber:{type:'integer'}}, ['stepNumber']),
  schema('poc_record_stop', 'Stop recording and export a TypeScript test.',
    {status: {type: 'string', enum: ['SUCCESS','FAILED','BLOCKED']}, reason: str}, ['status']),
  schema('browser_switch_page', 'Select a known stable page alias from the returned pages.', {page: str}, ['page']),
  schema('browser_new_page', 'Explicitly open a new browser tab.', {url: str}),
  schema('browser_close_page', 'Close the current browser tab.'),
  schema('browser_assert', 'Verify UI after the action. Uses actual browser state, retries for 5s and records the assertion. target is an observed snapshot reference or selector. For URL checks, omit target.',
    {kind: {type: 'string', enum: ['visible','hidden','text','value','enabled','disabled','checked','unchecked','url']},
     target: str, expected: str, semanticTarget, targetDescriptor: {type:'object', properties:{
       id:str, role:str, name:str, label:str, text:str, selector:str}, additionalProperties:false}}, ['kind']),
];

export function loadFork(input) {
  if (!input) throw new Error('PLAYWRIGHT_FORK_PATH is required');
  const root = fs.realpathSync(input);
  const core = fs.existsSync(path.join(root, 'packages/playwright-core/package.json'))
    ? fs.realpathSync(path.join(root, 'packages/playwright-core')) : root;
  const pkg = JSON.parse(fs.readFileSync(path.join(core, 'package.json'), 'utf8'));
  if (pkg.name !== 'playwright-core') throw new Error('Fork path must contain built playwright-core');
  const bundle = fs.realpathSync(path.join(core, 'lib/coreBundle.js'));
  if (!bundle.startsWith(core + path.sep)) throw new Error('Fork bundle resolves outside configured fork');
  // Absolute require: Node cannot fall back to an npm playwright-core installation.
  const playwright = require(path.join(core, 'index.js'));
  const {tools} = require(bundle);
  if (!tools?.createConnection || !tools?.Tab?.forPage) throw new Error('Fork lacks required current MCP APIs; rebuild it');
  return {playwright, tools, provenance: {corePath: core, version: pkg.version,
    bundleSha256: crypto.createHash('sha256').update(fs.readFileSync(bundle)).digest('hex')}};
}

export class Runtime {
  constructor(fork) {
    this.fork = fork;
    this.guard = new SecretGuard();
    this.journal = new Journal();
    this.pages = new Map();
    this.nextPage = 0;
    this.evidence = 0;
    this.verified = false;
    this.events = [];
    this.mcpCalls = 0;
    this.snapshotCount = 0;
    this.snapshotFallbackCount = 0;
    this.navigationVersion = 0;
    this.topologyVersion = 0;
    this.step = null;
    this.refDescriptors = new Map();
    this.logLevel = (process.env.LOG_LEVEL || 'INFO').toUpperCase();
    this.logLocators = process.env.LOG_LOCATORS?.toLowerCase() === 'true';
    this.logEvents = [];
    this.stateVersion = 0;
  }
  get logging() { return this.logLevel === 'DEBUG' || this.logLocators; }
  emit(line) { if (this.logging) this.logEvents.push(this.guard.clean(line)); }
  drainLogEvents() { return this.logEvents.splice(0); }
  safeActiveUrl() {
    const alias = this.pages.get(this.active)?.alias || 'unknown';
    return (this.pageState().find(page => page.alias === alias)?.url || 'about:blank')
      .replace(/(\/(?:access[_-]?token|refresh[_-]?token|api[_-]?key|token|session|auth|secret|key)\/)[^/?#]+/gi, '$1[REDACTED]');
  }
  stateLine() { return `[STATE] v${this.stateVersion} page=${this.pages.get(this.active)?.alias || 'unknown'} url=${this.safeActiveUrl()}`; }
  capturedSnapshot() { if (this.logLevel === 'DEBUG') this.emit(`[SNAPSHOT] captured state=v${this.stateVersion}`); }
  async currentFormValues() {
    return this.active.evaluate(() =>
      [...document.querySelectorAll('input,textarea,select,[contenteditable]')]
        .map(element => 'value' in element ? element.value : element.textContent).filter(Boolean));
  }
  async logRawSnapshot(snapshot) {
    if (this.logLevel !== 'DEBUG' || snapshot === undefined) return;
    let raw;
    try {
      const inputValues = await this.currentFormValues();
      raw = debugSnapshot(snapshot, inputValues, this.guard);
    } catch {
      raw = '<unavailable: form values could not be checked safely>';
    }
    this.emit(`[SNAPSHOT] page=${this.pages.get(this.active)?.alias || 'unknown'} state=v${this.stateVersion} url=${this.safeActiveUrl()}`);
    this.emit('[SNAPSHOT_RAW_BEGIN]');
    this.emit(raw);
    this.emit('[SNAPSHOT_RAW_END]');
  }
  async assertionActual(kind, locator) {
    if (kind === 'url') return {url:this.safeActiveUrl()};
    if (!locator) return null;
    try {
      const code = `async (page) => {
        const target = ${locator};
        const count = await target.count();
        if (count !== 1) return {count, visible: count === 0 ? false : null};
        const actual = {count};
        try { actual.visible = await target.isVisible(); } catch {}
        try { actual.selected = await target.getAttribute('aria-selected'); } catch {}
        try { if (actual.selected === null) actual.selected = await target.getAttribute('selected'); } catch {}
        try { actual.text = await target.innerText(); } catch {}
        return actual;
      }`;
      const response = await this.raw(this.runCodeName, {code});
      return response.isError ? null : JSON.parse(response.result || 'null');
    } catch { return null; }
  }
  async logAssertionFailure(args, locator, reason, initialValues) {
    try {
      const actual = await this.assertionActual(args.kind, locator);
      let inputValues = initialValues;
      try { inputValues = [...initialValues, ...await this.currentFormValues()]; }
      catch { if (actual) delete actual.text; }
      this.logEvents.push(this.guard.clean(assertionFailure(args, locator, actual, reason,
        inputValues, this.guard)));
    } catch {
      // Diagnostics must never replace the assertion's original outcome.
      this.logEvents.push('[ASSERT_FAILURE]\n- assertion kind: <unavailable>\n- actual state: <unavailable>');
    }
  }
  async initialize() {
    const launch = {headless: process.env.HEADLESS !== 'false'};
    if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) launch.executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
    this.browser = await this.fork.playwright.chromium.launch(launch);
    this.context = await this.browser.newContext({acceptDownloads: false});
    this.context.on('page', page => this.register(page));
    this.upstream = await this.fork.tools.createConnection({
      browser: {browserName: 'chromium', isolated: false}, capabilities: ['testing'],
      codegen: 'typescript', saveSession: false, saveTrace: false, imageResponses: 'omit',
      snapshot: {mode: 'none'}, secrets: this.guard.values,
      timeouts: {action: 10000, navigation: 30000},
    }, async () => this.context);
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    await this.upstream.connect(serverTransport);
    this.client = new Client({name:'jira-fork-bridge',version:'0.1.0'});
    await this.client.connect(clientTransport);
    const listed = await this.client.listTools();
    this.upstreamTools = listed.tools;
    this.runCodeName = listed.tools.find(t => ['browser_run_code_unsafe','browser_run_code'].includes(t.name))?.name;
    if (!this.runCodeName) throw new Error('Fork MCP lacks trusted code execution required for assertions');
    this.tools = [...listed.tools.filter(t => ALLOWED.has(t.name)).map(t => {
      const copy = structuredClone(t);
      delete copy.inputSchema.properties?.filename;
      delete copy.inputSchema.$schema;
      if (copy.name === 'browser_snapshot') {
        copy.description = 'Inspect a relevant visible target; defaults to last visible modal, otherwise page. Use scope=page explicitly to inspect outside a modal. Ambiguous scope falls back safely.';
        copy.inputSchema.properties.scope = {type:'string',enum:['auto','page']};
      }
      if (['browser_click','browser_type','browser_select_option'].includes(copy.name)) {
        copy.inputSchema.properties.semanticTarget = semanticTarget;
        copy.inputSchema.required = (copy.inputSchema.required || []).filter(item => item !== 'target');
      }
      return copy;
    }), ...CONTROL_TOOLS];
    this.active = await this.context.newPage();
    // Force upstream backend creation before obtaining its context.
    await this.raw('browser_tabs', {action: 'list'});
    this.hardenContext();
  }
  register(page) {
    const alias = this.nextPage++ === 0 ? 'page' : `page${this.nextPage-1}`;
    const info = {alias, page, opener: null};
    this.pages.set(page, info);
    this.topologyVersion++;
    page.on('close', () => this.topologyVersion++);
    page.on('framenavigated', frame => {
      this.navigationVersion++;
      if (frame === page.mainFrame()) this.topologyVersion++;
    });
    const entry = this.inflight?.mutating ? this.inflight : this.lastMutation;
    if (this.journal.active) {
      if (!entry) this.journal.problem = 'Page opened without a recordable originating action';
      else entry.pages.push(info);
    }
    this.events.push(page.opener().then(opener => {
      info.opener = this.pages.get(opener)?.alias || null;
      this.emit(`[PAGE] new page detected: ${alias} opener=${info.opener || 'none'}`);
    }));
    page.on('dialog', dialog => {
      this.dialog = {dialog, page, origin: this.inflight?.mutating ? this.inflight : this.lastMutation};
    });
    // Pages may log before the first MCP call. Harden as soon as MCP registers its tab.
    queueMicrotask(() => this.hardenContext());
    this.newest = page;
  }
  hardenContext() {
    for (const page of this.context.pages()) {
      const tab = this.fork.tools.Tab.forPage(page);
      if (!tab) continue;
      this.mcpContext = tab.context;
      // Version-dependent adapter: suppress automatic console files and all incidental
      // snapshot/download output. No raw files are ever routed into user artifacts.
      tab._consoleLog?.stop();
      tab.context.outputFile = async () => os.devNull;
    }
  }
  async raw(name, args = {}) {
    this.mcpCalls++;
    // In the inspected release, JSON mode implies raw mode and omits generated
    // code. Use it only for explicit snapshots. Parse action responses using the
    // fork's own parser, without cwd (which would allow reading linked files).
    const json = name === 'browser_snapshot';
    const result = await this.client.callTool({name, arguments: {...args, _meta: {json}}});
    if (json) {
      const payload = JSON.parse(result.content.filter(c => c.type === 'text').map(c => c.text).join('\n'));
      await this.logRawSnapshot(payload.snapshot);
      return {...payload, isError:Boolean(result.isError || payload.isError)};
    }
    const parsed = this.fork.tools.parseResponse(result);
    if (!parsed) throw new Error('Fork MCP response contract differs');
    return {result:parsed.result, error:parsed.error, 'ran playwright code':parsed.code,
      snapshot:parsed.inlineSnapshot, 'modal state':parsed.modalState, isError:Boolean(parsed.isError)};
  }
  async select(page) {
    const tab = this.fork.tools.Tab.forPage(page);
    if (!tab) throw new Error('MCP did not register the new page');
    const index = tab.context.tabs().findIndex(t => t.page === page);
    const result = await this.raw('browser_tabs', {action:'select', index});
    if (result.isError) throw new Error('Cannot select page in MCP');
    this.active = page;
    this.emit(`[PAGE] active page -> ${this.pages.get(page)?.alias || 'unknown'}`);
  }
  async synchronize() {
    await Promise.all(this.events.splice(0));
    this.hardenContext();
    const open = this.context.pages();
    if (this.newest && open.includes(this.newest) && this.newest !== this.active) await this.select(this.newest);
    this.newest = null;
    if (this.active?.isClosed()) {
      const previous = this.active;
      this.active = this.mcpContext?.currentTab()?.page;
      if (this.active && this.active !== previous)
        this.emit(`[PAGE] active page -> ${this.pages.get(this.active)?.alias || 'unknown'}`);
    }
  }
  pageState() {
    return [...this.pages.values()].map(info => {
      let url = info.page.url();
      try { const parsed = new URL(url); parsed.search = ''; parsed.hash = ''; parsed.username = ''; parsed.password = ''; url = parsed.toString(); } catch {}
      return {alias: info.alias, opener: info.opener, closed: info.page.isClosed(), url};
    });
  }
  async preflight() {
    if (this.journal.active) throw new Error('Preflight cannot run while recording');
    const page = this.active;
    // Fixture setup only; every tested action and locator is obtained through real MCP.
    await page.setContent('<label for="poc-stable-input">Code</label><input id="poc-stable-input"><button id="poc-stable-button">Finalize</button>');
    const snapshot = await this.raw('browser_snapshot', {});
    const findRef = (node, role, name) => {
      if (Array.isArray(node)) return node.map(child => findRef(child, role, name)).find(Boolean);
      if (node && typeof node === 'object') {
        if (node.role === role && node.name === name) return node.ref;
        return findRef(node.children, role, name);
      }
      if (typeof node === 'string') return node.match(new RegExp(`${role}[^\\n]*${name}[^\\n]*\\[ref=(e\\d+)\\]`))?.[1];
    };
    const button = findRef(snapshot.snapshot, 'button', 'Finalize');
    const input = findRef(snapshot.snapshot, 'textbox', 'Code');
    if (!button || !input) throw new Error('Fork snapshot contract differs; ID-first preflight cannot proceed');
    const click = await this.raw('browser_click', {target: button, element:'Finalize'});
    const fill = await this.raw('browser_type', {target: input, element:'Code', text:'POC_PREFLIGHT'});
    const clickCode = click['ran playwright code'] || '';
    const fillCode = fill['ran playwright code'] || '';
    this.verified = !click.isError && !fill.isError &&
      /locator\(['"]#poc-stable-button['"]\)/.test(clickCode) &&
      /locator\(['"]#poc-stable-input['"]\)/.test(fillCode);
    await page.goto('about:blank');
    if (!this.verified) return {isError:true, error:'Configured Playwright did not generate stable ID locators. No fallback is allowed.',
      idFirstVerified:false, ...this.fork.provenance, observedCode:[clickCode,fillCode]};
    return {idFirstVerified:true, ...this.fork.provenance, observedCode:[clickCode,fillCode]};
  }
  async assertion(args, onResolved = () => {}) {
    const kinds = new Set(['visible','hidden','text','value','enabled','disabled','checked','unchecked','url']);
    if (!kinds.has(args.kind)) throw new Error('Unsupported assertion kind');
    const pageAlias = this.pages.get(this.active)?.alias;
    let semantic = {...(this.refDescriptors.get(`${pageAlias}:${args.target}`) || {}),
      ...descriptor(args.targetDescriptor)};
    if (args.kind !== 'url' && !args.target && !hasUsableDescriptor(semantic))
      throw new Error('Assertion requires target or targetDescriptor');
    const attempt = async target => {
      let expression = '';
      if (args.kind !== 'url') {
        if (!target) throw new Error('Assertion requires target');
        const generated = await this.raw('browser_generate_locator', {target});
        if (generated.isError) return generated;
        expression = generated.result;
        if (!expression || !/^(page\.)?(locator|getBy|frameLocator)/.test(expression)) throw new Error('Fork locator generation contract differs');
        if (!expression.startsWith('page.')) expression = 'page.' + expression;
      }
      onResolved(expression || undefined);
      if (['text','value','url'].includes(args.kind) && typeof args.expected !== 'string') throw new Error('Assertion needs expected string');
      const expected = JSON.stringify(args.expected ?? '');
      const checks = {visible:'await target.isVisible()', hidden:'!(await target.isVisible())',
        enabled:'await target.isEnabled()', disabled:'!(await target.isEnabled())',
        checked:'await target.isChecked()', unchecked:'!(await target.isChecked())',
        text:`(await target.innerText()).includes(${expected})`, value:`(await target.inputValue()) === ${expected}`,
        url:`page.url() === ${expected}`};
      const body = `${expression ? `const target = ${expression};` : ''}
        const deadline = Date.now() + 5000;
        while (true) {
          if (${checks[args.kind]}) return {verified: true};
          if (Date.now() >= deadline) throw new Error('Expected result assertion failed');
          await new Promise(resolve => setTimeout(resolve, 100));
        }`;
      const result = await this.raw(this.runCodeName, {code:`async (page) => { ${body} }`});
      result.resolvedLocator = expression || undefined;
      let verified = false;
      try { verified = JSON.parse(result.result || '{}').verified === true; } catch {}
      if (!verified) result.isError = true;
      return result;
    };
    let target = args.target;
    const notices = [];
    if (args.kind !== 'url' && !target) {
      notices.push('[ASSERT] Resolving targetDescriptor on active page');
      let resolved;
      if (semantic.id || semantic.selector) {
        try { resolved = await resolveAssertionTarget(this.active, null, semantic); }
        catch { resolved = null; }
      }
      if (!resolved) {
        let fresh;
        try { fresh = await inspectSnapshot(this, {scope:'page', depth:12}); }
        catch { fresh = {isError:true}; }
        if (fresh.isError || !fresh.snapshot) return {isError:true, errorClass:'AssertionTargetError',
          error:'Current page snapshot unavailable for targetDescriptor', notices};
        try { resolved = await resolveAssertionTarget(this.active, fresh.snapshot, semantic); }
        catch { resolved = null; }
      }
      if (!resolved) return {isError:true, errorClass:'AssertionTargetError',
        error:'targetDescriptor could not be resolved uniquely on the active page', notices};
      target = resolved.target;
      semantic = resolved.targetDescriptor;
      notices.push(`[ASSERT] Resolved target to ${target}`);
    }
    let result;
    try { result = await attempt(target); }
    catch (error) {
      if (!staleRef(error)) throw error;
      result = {isError:true, error:error.message, errorClass:error.name};
    }
    const stale = staleRef(result);
    if (stale && args.kind !== 'url') {
      notices.push(`[ASSERT] Snapshot ref ${stale} is stale`, '[ASSERT] Capturing fresh snapshot');
      let fresh;
      try { fresh = await inspectSnapshot(this, {scope:'page', depth:12}); }
      catch { fresh = {isError:true}; }
      if (fresh.isError || !fresh.snapshot) return {isError:true, recoveryIncomplete:true,
        error:'Fresh page snapshot unavailable for stale assertion ref', errorClass:'AssertionRecoveryError', notices};
      let resolved;
      try { resolved = await resolveAssertionTarget(this.active, fresh.snapshot, semantic); }
      catch { resolved = null; }
      if (!resolved) return {isError:true, recoveryIncomplete:true,
        error:'Target could not be resolved uniquely in fresh page snapshot', errorClass:'AssertionRecoveryError', notices};
      target = resolved.target;
      semantic = resolved.targetDescriptor;
      notices.push(`[ASSERT] Re-resolved target to ${target}`, '[ASSERT] Retrying assertion');
      try { result = await attempt(target); }
      catch (error) { result = {isError:true, error:error.message, errorClass:error.name}; }
    }
    if (notices.length) result.notices = [...notices, ...(result.notices || [])];
    if (!result.isError) {
      result.evidenceId = `assertion-${++this.evidence}`;
      result.assertion = {kind:args.kind, target, expected:args.expected,
        ...(Object.keys(semantic).length ? {targetDescriptor:semantic} : {})};
    }
    return result;
  }
  async call(name, args = {}) {
    if (name === 'browser_semantic_plan') {
      if (!this.journal.active) throw new Error('Start verified recording before browser actions');
      if (this.guard.hasSecret(JSON.stringify(args))) throw new Error('Use environment variable names, not raw secrets');
      return executeSemanticPlan(this,args);
    }
    if (args.semanticTarget && ['browser_click','browser_type','browser_select_option','browser_assert'].includes(name)) {
      const type = {browser_click:'click',browser_type:'fill',browser_select_option:'select',browser_assert:'assert'}[name];
      const action = {type,target:args.semanticTarget};
      if (type === 'assert') { action.kind = args.kind; action.expected = args.expected; }
      if (type === 'fill') action.value = args.text;
      if (type === 'select') action.value = args.values?.[0];
      return this.call('browser_semantic_action',{action});
    }
    if (name === 'browser_batch' || (name === 'browser_fill_form' && args.fields?.length > 1)) {
      if (!this.journal.active) throw new Error('Start verified recording before browser actions');
      return executeBatch(this, name === 'browser_batch' ? args : {actions:[{tool:name,arguments:args}]});
    }
    if (name === 'poc_preflight') return this.preflight();
    if (name === 'poc_record_start') {
      if (!this.verified) throw new Error('ID-first fork preflight has not passed');
      if (this.journal.active || this.hasRecorded) throw new Error('One recording per isolated MCP session is supported');
      this.hasRecorded = true;
      this.lastMutation = null;
      this.journal.start();
      this.drainLogEvents();
      return {started:true, metrics:this.metrics(), activePage:this.pages.get(this.active)?.alias, pages:this.pageState()};
    }
    if (name === 'poc_step_start') {
      if (!this.journal.active) throw new Error('Start verified recording before Jira steps');
      if (!Number.isInteger(args.stepNumber)) throw new Error('Jira step number is required');
      await this.synchronize();
      this.step = {number:args.stepNumber, lockedPage:this.pages.get(this.active)?.alias,
        switchVersion:this.topologyVersion, revision:0, snapshotRevision:null,
        snapshotTopology:null, snapshotPage:null};
      if (this.logLevel === 'DEBUG') this.emit(this.stateLine());
      return {activePage:this.step.lockedPage, pages:this.pageState(), metrics:this.metrics(),
        logEvents:this.drainLogEvents()};
    }
    if (name === 'poc_record_stop') {
      if (!this.journal.active) throw new Error('Recording not started');
      await this.synchronize();
      if (this.dialog) this.journal.problem = 'Unresolved dialog; full replay cannot be guaranteed';
      const code = this.guard.assertCode(this.journal.render(args.status, args.reason || ''));
      return {code, available:true, complete:!this.journal.problem, strategy:'fork-mcp-action-code', syntaxValidated:true,
        mcpToolCalls:this.mcpCalls,
        metrics:this.metrics(),
        actionCount:this.journal.entries.filter(e => e.code).length, pages:this.pageState()};
    }
    if (!this.journal.active) throw new Error('Start verified recording before browser actions');
    if (!this.tools.some(t => t.name === name)) throw new Error('Tool is not exposed');
    if ('filename' in args || '_meta' in args) throw new Error('File output and raw metadata are forbidden');
    if (this.guard.hasSecret(JSON.stringify(args))) throw new Error('Use environment variable names, not raw secrets');
    if (['browser_navigate','browser_new_page'].includes(name) && args.url) {
      const url = new URL(args.url);
      if ((!['http:','https:'].includes(url.protocol) && args.url !== 'about:blank') || url.username || url.password)
        throw new Error('Navigation requires HTTP(S) without embedded credentials');
    }
    await this.synchronize();
    const alias = this.pages.get(this.active)?.alias;
    if (!alias && name !== 'browser_new_page') throw new Error('No active page');
    if (name === 'browser_snapshot' && this.step && this.step.snapshotRevision === this.step.revision &&
        this.step.snapshotTopology === this.topologyVersion && this.step.snapshotPage === alias) {
      return {isError:false, unchanged:true, reason:'UNCHANGED: The active page and browser state have not changed. Use the existing inspection to assert, take a meaningful action, or finish BLOCKED.',
        activePage:alias, pages:this.pageState(), metrics:this.metrics(), logEvents:this.drainLogEvents()};
    }
    if (name === 'browser_switch_page' && this.step &&
        (args.page === alias || this.topologyVersion <= this.step.switchVersion)) {
      return {isError:false, denied:true, reason:`PAGE_LOCKED: ${alias} remains active for Jira step ${this.step.number}; no new page or navigation requires a switch. Assert on this page or finish BLOCKED.`,
        activePage:alias, pages:this.pageState(), metrics:this.metrics(), logEvents:this.drainLogEvents()};
    }
    const mutating = name === 'browser_semantic_action' ? args.action?.type !== 'assert'
      : !READ.has(name) && name !== 'browser_assert';
    const beforeScreen = mutating ? await screenStamp(this) : undefined;
    const previousMutation = this.lastMutation;
    const entry = this.journal.begin(alias || 'page', name, mutating);
    this.inflight = entry;
    if (mutating) this.lastMutation = entry;
    let result;
    let actionLogged = false;
    let assertionInputs = [];
    let assertionLocator;
    let assertionFailureLogged = false;
    try {
      if (name === 'browser_snapshot') {
        result = await inspectSnapshot(this, args);
      } else if (name === 'browser_switch_page') {
        const info = [...this.pages.values()].find(p => p.alias === args.page && !p.page.isClosed());
        if (!info) throw new Error('Unknown page alias');
        await this.select(info.page);
        result = {isError:false};
      } else if (name === 'browser_new_page') {
        result = await this.raw('browser_tabs', {action:'new'});
        // Page creation is recorded from real context events; upstream tab code may
        // redeclare page. The event-driven journal supplies the new page variable.
        result['ran playwright code'] = 'await context.newPage();';
        await this.synchronize();
        if (args.url && !result.isError) {
          // Separate entry after page declaration so navigation has a bound alias.
          entry.code = result['ran playwright code'];
          const next = this.journal.begin(this.pages.get(this.active).alias, 'browser_navigate', true);
          this.inflight = next;
          this.lastMutation = next;
          const nav = await this.raw('browser_navigate', {url:args.url});
          next.code = nav['ran playwright code'] || '';
          result = {...nav, 'ran playwright code': entry.code};
        }
      } else if (name === 'browser_close_page') {
        result = await this.raw('browser_tabs', {action:'close'});
        result['ran playwright code'] = 'await page.close();';
      } else if (name === 'browser_assert') {
        try { assertionInputs = await this.currentFormValues(); } catch {}
        this.logEvents.push(this.guard.clean(assertionRequest(args, alias, assertionInputs, this.guard)));
        result = await this.assertion(args, locator => {
          assertionLocator = locator;
          this.logEvents.push(this.guard.clean(assertionResolved(locator, assertionInputs, this.guard)));
        });
        if (result.isError) {
          await this.logAssertionFailure(args, result.resolvedLocator || assertionLocator,
            result.error, assertionInputs);
          assertionFailureLogged = true;
        }
      } else if (name === 'browser_semantic_action') {
        result = await executeSemanticAction(this,args.action,entry);
      } else {
        result = await this.raw(name, args);
      }
      let code = result['ran playwright code'] || '';
      if (name === 'browser_handle_dialog' && !result.isError) {
        if (!this.dialog?.origin) throw new Error('Dialog has no originating action');
        const owner = this.pages.get(this.dialog.page).alias;
        const action = args.accept ? `accept(${JSON.stringify(args.promptText || '')})` : 'dismiss()';
        this.dialog.origin.before.push(`${owner}.once('dialog', dialog => { void dialog.${action}; });`);
        this.dialog = null;
        code = '';
      }
      // Failed calls may have performed partial work; retain available upstream code
      // and mark the final spec failed instead of claiming a complete recording.
      entry.code = this.guard.assertCode(code);
      if (result.denied && mutating) this.lastMutation = previousMutation;
      if (!result.isError && !result.denied && mutating && !code && !['browser_switch_page','browser_handle_dialog'].includes(name))
        throw new Error('Action succeeded without generated code; recording is incomplete');
      await this.synchronize();
      if (name === 'browser_semantic_action' && !result.isError && !result.denied && mutating) {
        try {
          const inventory = await uiInventory(this.active);
          result.inventoryCount = inventory.length;
          this.emit(`[INVENTORY] candidateCount=${inventory.length}`);
          if (inventory.some(item => item.role === 'dialog' && item.visible))
            this.emit('[UI] dialog detected');
        } catch { this.emit('[INVENTORY] unavailable after action'); }
      }
      if (this.logging && name !== 'browser_snapshot' && name !== 'browser_semantic_action') {
        const actionLines = actionLogLines(name, alias, code, result.resolvedLocator, args,
          this.guard, this.logLocators);
        this.logEvents.unshift(...actionLines);
        actionLogged = true;
      }
      if (mutating && !result.isError && !result.denied) {
        this.stateVersion++;
        if (this.logLevel === 'DEBUG') this.emit(this.stateLine());
      }
      if (this.step && !result.isError && !result.denied) {
        if (mutating) this.step.revision++;
        if (name === 'browser_snapshot') {
          this.step.snapshotRevision = this.step.revision;
          this.step.snapshotTopology = this.topologyVersion;
          this.step.snapshotPage = this.pages.get(this.active)?.alias;
        }
        if (name === 'browser_switch_page') {
          this.step.lockedPage = this.pages.get(this.active)?.alias;
          this.step.switchVersion = this.topologyVersion;
        }
      }
      if (result.snapshot) rememberSnapshotRefs(this.refDescriptors,
        this.pages.get(this.active)?.alias, result.snapshot);
      const compact = {isError:result.isError, executed:!result.isError && !result.denied &&
        (mutating || name === 'browser_assert' || name === 'browser_semantic_action'), mutating:mutating && !result.denied,
        result:result.result, error:result.error, snapshot:result.snapshot,
        modalState:result['modal state'], evidenceId:result.evidenceId, assertion:result.assertion,
        denied:result.denied, needsDecision:result.needsDecision, reason:result.reason,
        resolverStatus:result.resolverStatus,
        candidates:result.candidates, inventoryCount:result.inventoryCount,
        activePage:this.pages.get(this.active)?.alias, pages:this.pageState(),
        recoverable:result.recoverable, recoveryIncomplete:result.recoveryIncomplete,
        notices:result.notices, snapshotScope:result.snapshotScope,
        snapshotFallback:result.snapshotFallback,
        errorClass:result.isError?(result.errorClass || 'PlaywrightError'):undefined,
        transition:mutating && !result.denied && (beforeScreen !== await screenStamp(this) || !beforeScreen ||
          ['browser_click','browser_press_key','browser_handle_dialog'].includes(name)), metrics:this.metrics(),
        logEvents:this.drainLogEvents()};
      Object.assign(compact, compactSnapshot(compact.snapshot));
      if (compact.snapshotTruncated) compact.hint = 'Snapshot trimmed. Inspect a specific target or increase depth for omitted content.';
      if (compact.isError) Object.assign(compact, this.guard.diagnostic({name:compact.errorClass,message:compact.error}, name, compact.activePage));
      return this.guard.object(compact);
    } catch (error) {
      if (name === 'browser_assert' && !assertionFailureLogged)
        await this.logAssertionFailure(args, result?.resolvedLocator || assertionLocator,
          error.message, assertionInputs);
      if (this.logging && !actionLogged && name !== 'browser_snapshot' && name !== 'browser_semantic_action')
        this.logEvents.unshift(...actionLogLines(name, alias, result?.['ran playwright code'],
          result?.resolvedLocator, args, this.guard, this.logLocators));
      if (mutating) this.journal.problem ||= 'An action could not be recorded completely';
      throw error;
    } finally { this.inflight = null; }
  }
  metrics() { return {mcpToolCalls:this.mcpCalls, snapshotCount:this.snapshotCount, snapshotFallbackCount:this.snapshotFallbackCount}; }
  async close() {
    await this.client?.close().catch(() => {});
    await this.upstream?.close().catch(() => {});
    await this.browser?.close().catch(() => {});
  }
}
