// Real browser + real upstream MCP contract test. No Gemini decisions are mocked
// into a production run. --upstream-contract explicitly tests an uncustomized
// installation and MUST observe rejection by the production ID-first gate.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { Runtime, loadFork } from '../bridge/runtime.mjs';

const upstreamOnly = process.argv.includes('--upstream-contract');
const core = process.env.PLAYWRIGHT_FORK_PATH;
const out = process.env.CONTRACT_OUTPUT || '/tmp/masyon-contract';
fs.mkdirSync(out, {recursive:true, mode:0o700});
// Fresh synthetic credentials; these are never printed or saved in artifacts.
process.env.WBS_USERNAME = 'fixture_' + crypto.randomBytes(8).toString('hex');
process.env.WBS_PASSWORD = 'fixture_' + crypto.randomBytes(12).toString('hex') + '"&';
const fixture = http.createServer((request, response) => {
  response.setHeader('Content-Type','text/html');
  if (request.url === '/details') response.end(`<!doctype html><h1 id="detail-title">Flight details</h1>
    <label for="sl-code">SL Code</label><input id="sl-code">
    <label for="status">Status</label><select id="status"><option>Draft</option><option>Ready</option></select>
    <button id="save" onclick="document.querySelector('#codes').textContent=document.querySelector('#sl-code').value">Save</button>
    <p id="codes"></p><button id="confirm" onclick="if(confirm('Confirm save?')) document.querySelector('#confirmed').textContent='Confirmed'">Confirm</button><p id="confirmed"></p>`);
  else if (request.url === '/flights') response.end(`<!doctype html><h1 id="flight-list">Flights</h1>
    <a id="open-flight" href="/details" target="_blank">Demo Flight</a>`);
  else response.end(`<!doctype html><label for="username">Username</label><input id="username">
    <label for="password">Password</label><input id="password" type="password">
    <button id="login" onclick="if(document.querySelector('#username').value && document.querySelector('#password').value) document.querySelector('#dashboard').hidden=false">Log in</button>
    <div id="dashboard" hidden><h1>Dashboard</h1><a id="flights-menu" href="/flights">Flights menu</a></div>
    <p id="hidden-marker" hidden>Hidden marker</p>`);
});
await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${fixture.address().port}`;
const runtime = new Runtime(loadFork(core));
let assertions = 0;
async function call(name, args={}) {
  const result = await runtime.call(name,args);
  assert.equal(result.isError, false, `${name}: ${JSON.stringify(result)}`);
  if (result.evidenceId) assertions++;
  return result;
}
async function ref(role,name) {
  const result = await call('browser_snapshot');
  const tree = JSON.parse(result.snapshot);
  function visit(node) {
    if (Array.isArray(node)) return node.map(visit).find(Boolean);
    if (node && typeof node === 'object') {
      if (node.role === role && node.name === name) return node.ref;
      return visit(node.children);
    }
  }
  const found = visit(tree); assert.ok(found, `Missing ${role}: ${name}`); return found;
}
try {
  await runtime.initialize();
  const preflight = await runtime.preflight();
  if (upstreamOnly) {
    assert.equal(preflight.idFirstVerified, false, 'This contract mode requires plain upstream behavior');
    await assert.rejects(() => runtime.call('poc_record_start'), /preflight/);
    // Exercise recording in isolation, without passing the production start gate.
    // This is a test-only direct object call, unavailable via the MCP protocol.
    runtime.journal.start();
  } else {
    assert.equal(preflight.idFirstVerified, true, 'Custom fork must pass ID-first gate');
    await runtime.call('poc_record_start');
  }
  await call('browser_navigate',{url:base});
  await call('browser_type',{target:await ref('textbox','Username'),text:'WBS_USERNAME'});
  await call('browser_type',{target:await ref('textbox','Password'),text:'WBS_PASSWORD'});
  await call('browser_click',{target:await ref('button','Log in')});
  await call('browser_assert',{kind:'visible',target:await ref('heading','Dashboard')});
  await call('browser_click',{target:await ref('link','Flights menu')});
  await call('browser_assert',{kind:'visible',target:await ref('heading','Flights')});
  const popup = await call('browser_click',{target:await ref('link','Demo Flight')});
  assert.equal(popup.activePage,'page1');
  assert.equal(popup.pages.find(p => p.alias === 'page1').opener,'page');
  await call('browser_assert',{kind:'visible',target:await ref('heading','Flight details')});
  await call('browser_type',{target:await ref('textbox','SL Code'),text:'AUTO_CONTRACT_001'});
  await call('browser_press_key',{key:'Tab'});
  await call('browser_select_option',{target:await ref('combobox','Status'),values:['Ready']});
  await call('browser_assert',{kind:'value',target:await ref('combobox','Status'),expected:'Ready'});
  await call('browser_click',{target:await ref('button','Save')});
  await call('browser_assert',{kind:'text',target:'#codes',expected:'AUTO_CONTRACT_001'});
  await call('browser_click',{target:await ref('button','Confirm')});
  await call('browser_handle_dialog',{accept:true});
  await call('browser_assert',{kind:'text',target:'#confirmed',expected:'Confirmed'});
  await call('browser_switch_page',{page:'page'});
  await call('browser_assert',{kind:'visible',target:await ref('heading','Flights')});
  await call('browser_new_page',{url:base});
  assert.equal(runtime.pages.get(runtime.active).alias,'page2');
  await call('browser_assert',{kind:'visible',target:await ref('button','Log in')});
  await call('browser_close_page');
  const recording = await runtime.call('poc_record_stop',{status:'SUCCESS'});
  assert.match(recording.code,/process\.env\[['"]WBS_PASSWORD['"]\]/);
  runtime.guard.assertCode(recording.code);
  const file = path.join(out,'recording.spec.ts');
  fs.writeFileSync(file,recording.code,{mode:0o600});
  // Negative verification checks real browser visibility, not mere DOM presence.
  await runtime.active.goto(base);
  const negative = await runtime.assertion({kind:'visible',target:'#hidden-marker'});
  assert.equal(negative.isError,true);
  assert.equal(negative.evidenceId,undefined);
  let replay = 'not requested';
  if (process.env.CONTRACT_REPLAY_CLI) {
    const configPath = path.join(out,'playwright.config.cjs');
    fs.writeFileSync(configPath,`module.exports = {testDir: ${JSON.stringify(out)}, testMatch: 'recording.spec.ts',
      timeout: 60000, reporter: 'line', outputDir: ${JSON.stringify(path.join(out,'replay-results'))},
      use: {headless:true, trace:'off', screenshot:'off', video:'off',
        launchOptions:{executablePath:${JSON.stringify(process.env.PLAYWRIGHT_EXECUTABLE_PATH)}}}};`);
    const result = await new Promise(resolve => {
      const child = spawn(process.execPath,[process.env.CONTRACT_REPLAY_CLI,'test','--config',configPath],{
        cwd:out,env:{...process.env,FORCE_COLOR:'0'},stdio:['ignore','pipe','pipe']});
      let text=''; child.stdout.on('data',chunk => text += chunk); child.stderr.on('data',chunk => text += chunk);
      child.on('close',code => resolve({code,text}));
    });
    if (result.code !== 0) throw new Error(runtime.guard.clean(result.text));
    replay = 'passed';
  }
  const report = {kind:upstreamOnly?'UPSTREAM_CONTRACT_ONLY':'CUSTOM_FORK_CONTRACT',
    realGeminiRun:false, preflight, assertions, popupAliases:true, secretScan:'passed',
    hiddenElementRejected:true, recordingSyntax:recording.syntaxValidated, replay};
  fs.writeFileSync(path.join(out,'contract-result.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
} finally {
  await runtime.close();
  await new Promise(resolve => fixture.close(resolve));
}
