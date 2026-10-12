import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {Runtime, loadFork} from '../bridge/runtime.mjs';

const enabled = Boolean(process.env.PLAYWRIGHT_FORK_PATH);
let runtime;
before(async () => {
  if (!enabled) return;
  runtime = new Runtime(loadFork(process.env.PLAYWRIGHT_FORK_PATH));
  await runtime.initialize();
  assert.equal((await runtime.preflight()).idFirstVerified, true);
  await runtime.call('poc_record_start');
});
after(async () => { await runtime?.close(); });
const live = (name, fn) => test(name, {skip:!enabled}, fn);

const tabPage = `<button id="sl-tab" role="tab" aria-label="SL Codes" aria-selected="false"
  onclick="this.setAttribute('aria-selected','true');document.querySelector('#sl-panel').hidden=false">SL Codes</button>
  <section id="sl-panel" role="tabpanel" aria-label="SL Codes" hidden>SL Codes screen</section>`;

live('snapshot then snapshot returns UNCHANGED without another MCP inspection', async () => {
  await runtime.active.setContent(tabPage);
  await runtime.call('poc_step_start',{stepNumber:3});
  const first = await runtime.call('browser_snapshot');
  assert.equal(first.isError,false);
  const snapshots = runtime.snapshotCount, mcp = runtime.mcpCalls;
  const second = await runtime.call('browser_snapshot');
  assert.equal(second.unchanged,true);
  assert.match(second.reason,/UNCHANGED/);
  assert.equal(second.snapshot,undefined);
  assert.equal(runtime.snapshotCount,snapshots);
  assert.equal(runtime.mcpCalls,mcp);
});

live('click allows one fresh snapshot, then a duplicate is denied', async () => {
  await runtime.active.setContent(tabPage);
  await runtime.call('poc_step_start',{stepNumber:3});
  await runtime.call('browser_snapshot');
  const clicked = await runtime.call('browser_click',{target:'#sl-tab'});
  assert.equal(clicked.isError,false);
  const before = runtime.snapshotCount;
  const fresh = await runtime.call('browser_snapshot');
  assert.equal(fresh.isError,false);
  assert.equal(fresh.unchanged,undefined);
  assert.match(fresh.snapshot,/SL Codes screen/);
  assert.equal(runtime.snapshotCount,before+1);
  const duplicate = await runtime.call('browser_snapshot');
  assert.equal(duplicate.unchanged,true);
  assert.equal(runtime.snapshotCount,before+1);
});

live('page1 stays active; page/page1 oscillation is denied without topology change', async () => {
  const opened = await runtime.call('browser_new_page');
  assert.equal(opened.activePage,'page1');
  await runtime.call('poc_step_start',{stepNumber:3});
  const mcp = runtime.mcpCalls;
  const denied = await runtime.call('browser_switch_page',{page:'page'});
  assert.equal(denied.denied,true);
  assert.equal(denied.activePage,'page1');
  assert.equal(runtime.mcpCalls,mcp);
  const same = await runtime.call('browser_switch_page',{page:'page1'});
  assert.equal(same.denied,true);
  assert.equal(runtime.pages.get(runtime.active).alias,'page1');
});

live('a real new tab permits one switch, then locks the selected page', async () => {
  await runtime.call('poc_step_start',{stepNumber:3});
  const opened = await runtime.call('browser_new_page');
  assert.equal(opened.activePage,'page2');
  const switched = await runtime.call('browser_switch_page',{page:'page1'});
  assert.equal(switched.isError,false);
  assert.equal(switched.activePage,'page1');
  const denied = await runtime.call('browser_switch_page',{page:'page2'});
  assert.equal(denied.denied,true);
  assert.equal(denied.activePage,'page1');
});

live('Step 3-style tab click and tabpanel assertion succeed on the locked page', async () => {
  await runtime.active.setContent(tabPage);
  await runtime.call('poc_step_start',{stepNumber:3});
  await runtime.call('browser_snapshot');
  assert.equal((await runtime.call('browser_click',{target:'#sl-tab'})).isError,false);
  const postClick = await runtime.call('browser_snapshot');
  assert.equal(postClick.isError,false);
  const result = await runtime.call('browser_assert',{
    kind:'visible',targetDescriptor:{role:'tabpanel',name:'SL Codes'}});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.ok(result.evidenceId);
  assert.equal(result.activePage,'page1');
});
