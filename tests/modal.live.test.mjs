import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {Runtime, loadFork} from '../bridge/runtime.mjs';

const enabled = Boolean(process.env.PLAYWRIGHT_FORK_PATH);
let runtime;
before(async () => {
  if (!enabled) return;
  runtime = new Runtime(loadFork(process.env.PLAYWRIGHT_FORK_PATH));
  await runtime.initialize();
  const preflight = await runtime.preflight();
  assert.equal(preflight.idFirstVerified,true, 'Configured custom fork must pass unchanged preflight');
  await runtime.call('poc_record_start');
});
after(async () => { await runtime?.close(); });
const live = (name, fn) => test(name, {skip:!enabled}, fn);
const contains = (result, text) => assert.ok(result.snapshot.includes(text), result.snapshot);
async function inspect(html, args={}) {
  await runtime.active.setContent(html);
  const result = await runtime.call('browser_snapshot',args);
  assert.equal(result.isError,false,JSON.stringify(result));
  return result;
}

live('one visible dialog excludes hidden modal and background navigation', async () => {
  const r = await inspect('<nav>IRRELEVANT_NAV</nav><div role="dialog" hidden>HIDDEN_DIALOG</div><div role="dialog"><button>Visible dialog</button></div>');
  contains(r,'Visible dialog'); assert.ok(!r.snapshot.includes('IRRELEVANT_NAV')); assert.ok(!r.snapshot.includes('HIDDEN_DIALOG'));
});
live('nested Ant modal nodes resolve a single visible role=dialog', async () => {
  const r = await inspect('<div class="ant-modal-root"><nz-modal-container role="dialog"><div class="ant-modal" role="document"><button>Nested modal</button></div></nz-modal-container></div>');
  assert.equal(r.snapshotScope,'target'); contains(r,'Nested modal');
});
live('multiple dialogs use last visible dialog', async () => {
  const r = await inspect('<div role="dialog">First dialog</div><div role="dialog">Last dialog</div>');
  contains(r,'Last dialog'); assert.ok(!r.snapshot.includes('First dialog')); assert.ok(r.notices.length);
});
live('Ant and generic containers work without role=dialog', async () => {
  for (const className of ['ant-modal','modal']) {
    const r = await inspect(`<nav>Outside</nav><div class="${className}"><button>Inside</button></div>`);
    contains(r,'Inside'); assert.ok(!r.snapshot.includes('Outside'));
  }
});
live('no dialog uses page; explicit scope=page can inspect outside a modal', async () => {
  const r = await inspect('<h1>Whole page</h1>'); assert.equal(r.snapshotScope,'page'); contains(r,'Whole page');
  contains(await inspect('<nav>Outside</nav><div role="dialog">Inside</div>',{scope:'page'}),'Outside');
});
live('reported broad union cannot fail snapshot: whole-page fallback is logged', async () => {
  const before = runtime.snapshotFallbackCount;
  const r = await inspect('<nz-modal-container role="dialog"><div class="ant-modal" role="document"><button>Survived</button></div></nz-modal-container>',
    {target:'div.ant-modal-root, div.ant-modal, [role="dialog"]'});
  contains(r,'Survived'); assert.equal(r.snapshotFallback,true);
  assert.equal(runtime.snapshotFallbackCount,before+1); assert.ok(r.notices.some(n => n.includes('Falling back')));
});
live('scoped MCP strict-mode race falls back; whole-page inspection error is recoverable', async () => {
  await runtime.active.setContent('<div role="dialog"><button>Fallback</button></div>');
  const raw = runtime.raw.bind(runtime);
  try {
    runtime.raw = async (name,args) => name==='browser_snapshot' && args.target
      ? {isError:true,error:'strict mode violation: resolved to 2 elements'} : raw(name,args);
    const r = await runtime.call('browser_snapshot');
    assert.equal(r.isError,false); contains(r,'Fallback'); assert.equal(r.snapshotFallback,true);
    runtime.raw = async () => ({isError:true,error:'Temporary snapshot error'});
    const unavailable = await runtime.call('browser_snapshot');
    assert.equal(unavailable.recoverable,true); assert.equal(runtime.journal.problem,'');
  } finally { runtime.raw = raw; }
});
live('deterministic fills batch; click/modal transition stops remaining actions', async () => {
  await runtime.active.setContent('<input id="a"><input id="b"><button id="open" onclick="document.body.insertAdjacentHTML(\'beforeend\',\'<div role=dialog>Opened</div>\')">Open</button>');
  const r = await runtime.call('browser_batch',{actions:[
    {tool:'browser_type',arguments:{target:'#a',text:'one'}},
    {tool:'browser_type',arguments:{target:'#b',text:'two'}},
    {tool:'browser_click',arguments:{target:'#open'}},
    {tool:'browser_type',arguments:{target:'#a',text:'MUST_NOT_RUN'}}]});
  assert.equal(r.isError,false); assert.equal(r.batchExecuted,3); assert.equal(r.remainingActions,1);
  assert.equal(r.checkpointRequired,true); assert.equal(await runtime.active.locator('#a').inputValue(),'one');
  contains(await runtime.call('browser_snapshot'),'Opened');
});
live('unexpected validation after fill forces a batch checkpoint', async () => {
  await runtime.active.setContent('<input id="a" oninput="document.querySelector(\'p\').textContent=\'Unexpected validation\'"><input id="b"><p></p>');
  const r = await runtime.call('browser_batch',{actions:[
    {tool:'browser_type',arguments:{target:'#a',text:'one'}},
    {tool:'browser_type',arguments:{target:'#b',text:'MUST_NOT_RUN'}}]});
  assert.equal(r.batchExecuted,1); assert.equal(r.checkpointRequired,true);
  assert.equal(await runtime.active.locator('#b').inputValue(),'');
});
live('native bulk fill also stops between fields when validation changes the screen', async () => {
  await runtime.active.setContent('<input id="a" oninput="document.querySelector(\'p\').textContent=\'Validation\'"><input id="b"><p></p>');
  const r = await runtime.call('browser_fill_form',{fields:[
    {name:'First',type:'textbox',target:'#a',value:'one'},
    {name:'Second',type:'textbox',target:'#b',value:'MUST_NOT_RUN'}]});
  assert.equal(r.batchExecuted,1); assert.equal(r.remainingActions,1);
  assert.equal(await runtime.active.locator('#b').inputValue(),'');
});
