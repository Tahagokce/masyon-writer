import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {Runtime, loadFork} from '../bridge/runtime.mjs';
import {actionLocators} from '../bridge/action-logging.mjs';

const enabled = Boolean(process.env.PLAYWRIGHT_FORK_PATH);
let runtime;
before(async () => {
  if (!enabled) return;
  const level = process.env.LOG_LEVEL, locators = process.env.LOG_LOCATORS;
  process.env.LOG_LEVEL = 'DEBUG';
  process.env.LOG_LOCATORS = 'true';
  runtime = new Runtime(loadFork(process.env.PLAYWRIGHT_FORK_PATH));
  if (level === undefined) delete process.env.LOG_LEVEL; else process.env.LOG_LEVEL = level;
  if (locators === undefined) delete process.env.LOG_LOCATORS; else process.env.LOG_LOCATORS = locators;
  await runtime.initialize();
  assert.equal((await runtime.preflight()).idFirstVerified,true);
  await runtime.call('poc_record_start');
});
after(async () => { await runtime?.close(); });
const live = (name, fn) => test(name,{skip:!enabled},fn);

async function ref(role,name) {
  const snapshot = await runtime.call('browser_snapshot');
  assert.equal(snapshot.isError,false,JSON.stringify(snapshot));
  const visit = node => {
    if (Array.isArray(node)) return node.map(visit).find(Boolean);
    if (node && typeof node === 'object') {
      if (node.role === role && node.name === name) return node.ref;
      return visit(node.children);
    }
  };
  const found = visit(JSON.parse(snapshot.snapshot));
  assert.ok(found,`Missing ${role}: ${name}`);
  return {found,snapshot};
}

async function actionWithForkCode(tool,args) {
  const raw = runtime.raw.bind(runtime);
  let code;
  try {
    runtime.raw = async (name,arguments_) => {
      const result = await raw(name,arguments_);
      if (name === tool) code = result['ran playwright code'];
      return result;
    };
    const result = await runtime.call(tool,args);
    assert.equal(result.isError,false,JSON.stringify(result));
    assert.ok(code,`Fork did not return code for ${tool}`);
    return {result,code};
  } finally { runtime.raw = raw; }
}

live('DEBUG logs state and the snapshot version', async () => {
  await runtime.active.setContent('<button id="save">Save</button>');
  const started = await runtime.call('poc_step_start',{stepNumber:1});
  assert.ok(started.logEvents.some(line => /^\[STATE\] v0 page=page(?: url=)/.test(line)));
  const {found,snapshot} = await ref('button','Save');
  assert.ok(snapshot.logEvents.includes('[SNAPSHOT] captured state=v0'));
  assert.ok(snapshot.logEvents.includes('[SNAPSHOT_RAW_BEGIN]'));
  assert.ok(snapshot.logEvents.includes('[SNAPSHOT_RAW_END]'));
  assert.ok(snapshot.logEvents.some(line => line.includes('"name": "Save"')));
  const {result} = await actionWithForkCode('browser_click',{target:found});
  assert.ok(result.logEvents.some(line => /^\[STATE\] v1 page=page(?: url=)/.test(line)));
  const postClick = await runtime.call('browser_snapshot');
  assert.ok(postClick.logEvents.includes('[SNAPSHOT] captured state=v1'));
});

live('ID-first locator log equals the real fork-generated click receiver', async () => {
  await runtime.active.setContent('<button id="stable-button">Save</button>');
  await runtime.call('poc_step_start',{stepNumber:2});
  const {found} = await ref('button','Save');
  const {result,code} = await actionWithForkCode('browser_click',{target:found});
  const [actual] = actionLocators(code);
  assert.match(actual,/^page\.locator\(['"]#stable-button['"]\)$/);
  assert.ok(result.logEvents.includes(`[LOCATOR] ${actual}`));
  assert.ok(result.logEvents.includes('[ACTION] click'));
  assert.ok(result.logEvents.includes('[PAGE] page'));
});

live('role fallback locator log equals the real fork-generated click receiver', async () => {
  await runtime.active.setContent('<button role="tab" aria-label="SL Codes">SL Codes</button>');
  await runtime.call('poc_step_start',{stepNumber:3});
  const {found} = await ref('tab','SL Codes');
  const {result,code} = await actionWithForkCode('browser_click',{target:found});
  const [actual] = actionLocators(code);
  assert.match(actual,/^page\.getByRole\(['"]tab['"]/);
  assert.ok(result.logEvents.includes(`[LOCATOR] ${actual}`));
});

live('fill logs its locator and a masked marker, never its value', async () => {
  await runtime.active.setContent('<label for="password">Password</label><input id="password" type="password">');
  await runtime.call('poc_step_start',{stepNumber:4});
  const {found} = await ref('textbox','Password');
  const value = 'synthetic-private-value-314';
  const {result,code} = await actionWithForkCode('browser_type',{target:found,text:value});
  const [actual] = actionLocators(code);
  assert.match(actual,/^page\.locator\(['"]#password['"]\)$/);
  assert.ok(result.logEvents.includes(`[LOCATOR] ${actual}`));
  assert.ok(result.logEvents.includes('[VALUE] <masked>'));
  assert.equal(result.logEvents.join('\n').includes(value),false);
  const snapshot = await runtime.call('browser_snapshot');
  assert.equal(snapshot.isError,false);
  assert.equal(snapshot.logEvents.join('\n').includes(value),false);
  assert.ok(snapshot.logEvents.includes('[SNAPSHOT_RAW_BEGIN]'));
});

live('batched fills retain each real locator log and mask all values', async () => {
  await runtime.active.setContent('<input id="first"><input id="second">');
  await runtime.call('poc_step_start',{stepNumber:5});
  const result = await runtime.call('browser_fill_form',{fields:[
    {name:'First',type:'textbox',target:'#first',value:'private-first-value'},
    {name:'Second',type:'textbox',target:'#second',value:'private-second-value'}]});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.equal(result.batchExecuted,2);
  const locators = result.logEvents.filter(line => line.startsWith('[LOCATOR] '));
  assert.deepEqual(locators,["[LOCATOR] page.locator('#first')",
    "[LOCATOR] page.locator('#second')"]);
  assert.equal(result.logEvents.filter(line => line === '[VALUE] <masked>').length,2);
  assert.equal(result.logEvents.join('\n').includes('private-first-value'),false);
  assert.equal(result.logEvents.join('\n').includes('private-second-value'),false);
});

live('LOG_LOCATORS=false emits no locator logs', async () => {
  await runtime.active.setContent('<button id="quiet-button">Quiet</button>');
  await runtime.call('poc_step_start',{stepNumber:6});
  const {found} = await ref('button','Quiet');
  runtime.logLocators = false;
  try {
    const result = await runtime.call('browser_click',{target:found});
    assert.equal(result.isError,false,JSON.stringify(result));
    assert.equal(result.logEvents.some(line => line.startsWith('[LOCATOR]')),false);
  } finally { runtime.logLocators = true; }
});

live('new tabs and active page changes are logged with aliases', async () => {
  await runtime.active.setContent('<button id="open" onclick="window.open(\'about:blank\')">Open</button>');
  await runtime.call('poc_step_start',{stepNumber:7});
  const {found} = await ref('button','Open');
  const result = await runtime.call('browser_click',{target:found});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.ok(result.logEvents.includes('[PAGE] new page detected: page1 opener=page'));
  assert.ok(result.logEvents.includes('[PAGE] active page -> page1'));
  assert.ok(result.logEvents.some(line => /^\[STATE\] v\d+ page=page1 url=/.test(line)));
  const closed = await runtime.call('browser_close_page');
  assert.equal(closed.isError,false,JSON.stringify(closed));
  assert.ok(closed.logEvents.includes('[PAGE] active page -> page'));
  const recording = await runtime.call('poc_record_stop',{status:'SUCCESS'});
  assert.ok(!recording.code.includes('[LOCATOR]'));
});
