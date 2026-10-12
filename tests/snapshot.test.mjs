import test from 'node:test';
import assert from 'node:assert/strict';
import {compactSnapshot, debugSnapshot} from '../bridge/snapshot.mjs';
import {SecretGuard} from '../bridge/security.mjs';
import {Runtime} from '../bridge/runtime.mjs';
test('large snapshots stay valid JSON, bounded and visibly marked trimmed', () => {
  const tree = [{role:'generic',children:Array.from({length:500},(_,i)=>({role:'button',name:'button '+i,ref:'e'+i}))}];
  const result=compactSnapshot(tree);
  assert.ok(result.snapshot.length<=4000); assert.equal(result.snapshotTruncated,true);
  assert.ok(JSON.parse(result.snapshot)[0].children[0].ref);
});
test('diagnostics keep class/tool/page and actual safe message without secrets', () => {
  const guard=new SecretGuard([]);
  const r=guard.diagnostic(new Error('strict mode violation; url=https://test/?access_token=secret-token password="secret phrase"'), 'browser_snapshot','page1');
  assert.match(r.error,/strict mode violation/); assert.equal(r.errorClass,'Error'); assert.equal(r.activePage,'page1');
  assert.ok(!r.error.includes('secret-token')); assert.ok(!r.error.includes('secret phrase'));
});

test('DEBUG snapshot keeps the full MCP tree and masks form values', async () => {
  const runtime = new Runtime({});
  runtime.logLevel = 'DEBUG';
  runtime.stateVersion = 7;
  const page = {url:() => 'https://example.test/wbm?token=hidden',isClosed:() => false,
    evaluate:async () => ['private-fill']};
  runtime.pages.set(page,{alias:'page1',page,opener:'page'});
  runtime.active = page;
  const snapshot = [{role:'tab',name:'SL Codes',ref:'e137'},
    {role:'textbox',name:'Code',value:'private-fill',text:'private-fill'},
    {role:'generic',text:'x'.repeat(6000)}];
  runtime.client = {callTool:async () => ({content:[{type:'text',text:JSON.stringify({snapshot})}]})};
  const response = await runtime.raw('browser_snapshot',{});
  assert.deepEqual(response.snapshot,snapshot);
  const events = runtime.drainLogEvents();
  assert.equal(events[0],'[SNAPSHOT] page=page1 state=v7 url=https://example.test/wbm');
  assert.equal(events[1],'[SNAPSHOT_RAW_BEGIN]');
  assert.equal(events[3],'[SNAPSHOT_RAW_END]');
  assert.ok(events[2].includes('SL Codes'));
  assert.ok(events[2].includes('x'.repeat(6000)));
  assert.equal(events.join('\n').includes('private-fill'),false);
  assert.equal(snapshot[1].value,'private-fill');
});

test('non-DEBUG mode does not evaluate or log a raw snapshot', async () => {
  const runtime = new Runtime({});
  runtime.logLevel = 'INFO';
  runtime.active = {evaluate:() => { throw new Error('must not evaluate'); }};
  await runtime.logRawSnapshot([{role:'tab',name:'SL Codes'}]);
  assert.deepEqual(runtime.drainLogEvents(),[]);
});

test('DEBUG snapshot fails closed when live form values cannot be checked', async () => {
  const runtime = new Runtime({});
  runtime.logLevel = 'DEBUG';
  const page = {url:() => 'about:blank',isClosed:() => false,
    evaluate:async () => { throw new Error('navigation race'); }};
  runtime.pages.set(page,{alias:'page',page,opener:null});
  runtime.active = page;
  await runtime.logRawSnapshot([{role:'textbox',value:'private-fill'}]);
  const logged = runtime.drainLogEvents().join('\n');
  assert.ok(logged.includes('[SNAPSHOT_RAW_BEGIN]'));
  assert.ok(logged.includes('form values could not be checked safely'));
  assert.equal(logged.includes('private-fill'),false);
});

test('sensitive snapshot keys are redacted independently of form values', () => {
  const raw = {role:'generic',accessToken:'opaque',children:[{role:'textbox',value:'B'}]};
  const text = debugSnapshot(raw,[],new SecretGuard([]));
  assert.equal(text.includes('opaque'),false);
  assert.equal(text.includes('"B"'),false);
});
