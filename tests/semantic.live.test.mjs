import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {Runtime,loadFork} from '../bridge/runtime.mjs';
import {uiInventory} from '../bridge/ui-inventory.mjs';

const enabled = Boolean(process.env.PLAYWRIGHT_FORK_PATH);
let runtime;
before(async () => {
  if (!enabled) return;
  runtime = new Runtime(loadFork(process.env.PLAYWRIGHT_FORK_PATH));
  runtime.logLevel = 'DEBUG';
  runtime.logLocators = true;
  await runtime.initialize();
  assert.equal((await runtime.preflight()).idFirstVerified,true);
  await runtime.call('poc_record_start');
});
after(async () => { await runtime?.close(); });
const live = (name,fn) => test(name,{skip:!enabled},fn);

live('whole-page inventory includes sibling tabs and executes click plus semantic assertion',async () => {
  await runtime.active.setContent(`<div role="tablist">
    <button role="tab" id="commodity-tab" onclick="show('commodity')">Commodity Subtypes</button>
    <button role="tab" id="special-load-tab" onclick="show('codes')">SL Codes</button>
    <button role="tab" id="segregation-tab">Special Load Segregation</button>
    <button role="tab" id="uld-tab">ULD Characteristics</button></div>
    <section role="tabpanel" aria-label="Commodity Subtypes" id="commodity">Default</section>
    <section role="tabpanel" aria-label="SL Codes" id="codes" hidden>Rows</section>
    <script>function show(name) { document.querySelector('#commodity').hidden=name!=='commodity'; document.querySelector('#codes').hidden=name!=='codes'; }</script>`);
  const inventory = await uiInventory(runtime.active);
  assert.deepEqual(inventory.filter(x => x.role === 'tab').map(x => x.name),
    ['Commodity Subtypes','SL Codes','Special Load Segregation','ULD Characteristics']);
  const result = await runtime.call('browser_semantic_plan',{actions:[
    {type:'click',target:{name:'SL Codes',roleHint:'tab'}},
    {type:'assert',kind:'visible',target:{name:'SL Codes',roleHint:'tabpanel'}}]});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.equal(result.evidenceIds.length,1);
  assert.ok(result.logEvents.some(line => line === '[LOCATOR] page.locator("#special-load-tab")'));
  assert.equal(await runtime.active.locator('#codes').isVisible(),true);
  assert.equal(runtime.snapshotCount,0);
});

live('test ID, role, exact text and label fallbacks execute with their actual locators',async () => {
  await runtime.active.setContent(`<button data-testid="save-control" aria-label="Save">Store</button>
    <div role="tab" tabindex="0">Plans</div><div onclick="window.didClick=true">Continue</div>
    <label for="description">Description</label><input id="description">`);
  const actions = [
    {type:'click',target:{name:'Save',roleHint:'button'},prefix:'page.getByTestId("save-control")'},
    {type:'click',target:{name:'Plans',roleHint:'tab'},prefix:'page.getByRole("tab"'},
    {type:'click',target:{name:'Continue'},prefix:'page.getByText("Continue"'},
    {type:'fill',target:{name:'Description'},value:'sample',prefix:'page.locator("#description")'}];
  for (const {prefix,...action} of actions) {
    const result = await runtime.call('browser_semantic_action',{action});
    assert.equal(result.isError,false,JSON.stringify(result));
    assert.ok(result.logEvents.some(line => line.startsWith(`[LOCATOR] ${prefix}`)),JSON.stringify(result.logEvents));
  }
  assert.equal(await runtime.active.locator('#description').inputValue(),'sample');
});

live('dynamic ID falls back to role and ambiguous targets do not click',async () => {
  await runtime.active.setContent(`<button id="cdk-overlay-123" onclick="window.clicked=true">Apply</button>
    <button onclick="window.ambiguous=(window.ambiguous||0)+1">Save</button>
    <button onclick="window.ambiguous=(window.ambiguous||0)+1">Save</button>`);
  const resolved = await runtime.call('browser_semantic_action',{
    action:{type:'click',target:{name:'Apply',roleHint:'button'}}});
  assert.equal(resolved.isError,false,JSON.stringify(resolved));
  assert.ok(resolved.logEvents.some(line => line.startsWith('[LOCATOR] page.getByRole("button"')));
  const ambiguous = await runtime.call('browser_semantic_action',{
    action:{type:'click',target:{name:'Save',roleHint:'button'}}});
  assert.equal(ambiguous.needsDecision,true);
  assert.equal(ambiguous.resolverStatus,'AMBIGUOUS');
  assert.equal(ambiguous.executed,false);
  assert.equal(await runtime.active.evaluate(() => window.ambiguous || 0),0);
  const before = runtime.snapshotCount;
  const missing = await runtime.call('browser_semantic_action',{
    action:{type:'click',target:{name:'Unknown',roleHint:'button'}}});
  assert.equal(missing.resolverStatus,'NO_MATCH');
  assert.equal(runtime.snapshotCount,before);
});

live('label, select, checkbox and press actions use semantic targets',async () => {
  await runtime.active.setContent(`<label>Description<input placeholder="Enter description"></label>
    <label>Priority<select><option>Low</option><option>High</option></select></label>
    <label><input type="checkbox">Enabled</label><output id="pressed"></output>
    <script>document.querySelector('input[placeholder]').addEventListener('keydown', event => {
      if (event.key === 'Enter') document.querySelector('#pressed').textContent='Submitted';
    });</script>`);
  const operations = [
    {type:'fill',target:{name:'Description'},value:'sample',locator:'page.getByRole("textbox"'},
    {type:'select',target:{name:'Priority'},value:'High',locator:'page.getByRole("combobox"'},
    {type:'check',target:{name:'Enabled',roleHint:'checkbox'},locator:'page.getByRole("checkbox"'},
    {type:'press',target:{name:'Description'},key:'Enter',locator:'page.getByRole("textbox"'}];
  for (const {locator,...action} of operations) {
    const result = await runtime.call('browser_semantic_action',{action});
    assert.equal(result.isError,false,JSON.stringify(result));
    assert.ok(result.logEvents.some(line => line.startsWith(`[LOCATOR] ${locator}`)),JSON.stringify(result.logEvents));
  }
  assert.equal(await runtime.active.locator('select').inputValue(),'High');
  assert.equal(await runtime.active.locator('input[type=checkbox]').isChecked(),true);
  assert.equal(await runtime.active.locator('#pressed').innerText(),'Submitted');
  const unchecked = await runtime.call('browser_semantic_action',{
    action:{type:'uncheck',target:{name:'Enabled',roleHint:'checkbox'}}});
  assert.equal(unchecked.isError,false,JSON.stringify(unchecked));
  assert.equal(await runtime.active.locator('input[type=checkbox]').isChecked(),false);
});

live('legacy tool names accept a semantic target without a snapshot ref',async () => {
  await runtime.active.setContent('<button id="apply">Apply</button>');
  const result = await runtime.call('browser_click',{semanticTarget:{name:'Apply',roleHint:'button'}});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.ok(result.logEvents.some(line => line === '[LOCATOR] page.locator("#apply")'));
});

live('semantic fill masks password and locator logs obey LOG_LOCATORS',async () => {
  await runtime.active.setContent('<label>Password<input type="password" id="password"></label>');
  const value = 'synthetic-secret-for-semantic-test';
  runtime.guard.values.SEMANTIC_TEST_PASSWORD = value;
  runtime.guard.variants.add(value);
  runtime.logLocators = false;
  try {
    const result = await runtime.call('browser_semantic_action',{
      action:{type:'fill',target:{name:'Password'},value:'SEMANTIC_TEST_PASSWORD'}});
    assert.equal(result.isError,false,JSON.stringify(result));
    assert.equal(result.logEvents.some(line => line.startsWith('[LOCATOR]')),false);
    assert.equal(result.logEvents.join('\n').includes(value),false);
    assert.equal(await runtime.active.locator('#password').inputValue(),value);
  } finally {
    runtime.logLocators = true;
    delete runtime.guard.values.SEMANTIC_TEST_PASSWORD;
    runtime.guard.variants.delete(value);
  }
});

live('new page is selected automatically and inventoried after a semantic click',async () => {
  await runtime.active.setContent(`<button id="open" onclick="window.open('about:blank').document.write('<button id=&quot;popup-save&quot;>Save</button>')">Open</button>`);
  const result = await runtime.call('browser_semantic_action',{
    action:{type:'click',target:{name:'Open',roleHint:'button'}}});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.equal(result.activePage,'page1');
  assert.ok(result.logEvents.some(line => line.includes('new page detected: page1')));
  assert.ok((await uiInventory(runtime.active)).some(item => item.name === 'Save'));
  await runtime.call('browser_close_page');
});

live('semantic click detects a dialog and asserts it without a snapshot',async () => {
  await runtime.active.setContent(`<button id="create" onclick="document.querySelector('#form').hidden=false">Create</button>
    <div id="form" role="dialog" aria-label="New Definition" hidden><button>Save</button></div>`);
  const before = runtime.snapshotCount;
  const result = await runtime.call('browser_semantic_plan',{actions:[
    {type:'click',target:{name:'Create',roleHint:'button'}},
    {type:'assert',kind:'visible',target:{name:'New Definition',roleHint:'dialog'}}]});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.equal(result.evidenceIds.length,1);
  assert.equal(runtime.snapshotCount,before);
  assert.ok(result.logEvents.includes('[UI] dialog detected'));
});

live('delayed dialog resolves through inventory polling without a snapshot',async () => {
  await runtime.active.setContent(`<button id="open-later" onclick="setTimeout(() => document.querySelector('#later').hidden=false, 150)">Open later</button>
    <div role="dialog" aria-label="Delayed form" id="later" hidden>Ready</div>`);
  const before = runtime.snapshotCount;
  const result = await runtime.call('browser_semantic_plan',{actions:[
    {type:'click',target:{name:'Open later',roleHint:'button'}},
    {type:'assert',kind:'visible',target:{name:'Delayed form',roleHint:'dialog'}}]});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.equal(runtime.snapshotCount,before);
  assert.ok(result.logEvents.includes('[UI] dialog detected'));
});

live('navigate and URL assertion use the semantic executor',async () => {
  const before = runtime.snapshotCount;
  const result = await runtime.call('browser_semantic_plan',{actions:[
    {type:'navigate',url:'about:blank'},
    {type:'assert',kind:'url',expected:'about:blank'}]});
  assert.equal(result.isError,false,JSON.stringify(result));
  assert.equal(result.evidenceIds.length,1);
  assert.equal(runtime.snapshotCount,before);
});

live('recording contains the exact executed stable-ID locator',async () => {
  const recording = await runtime.call('poc_record_stop',{status:'SUCCESS'});
  assert.equal(recording.complete,true);
  assert.match(recording.code,/locator\("#special-load-tab"\)\.click\(\)/);
  assert.match(recording.code,/getByRole\("tab"/);
});
