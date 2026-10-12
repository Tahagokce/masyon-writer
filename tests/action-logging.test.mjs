import {test} from 'node:test';
import assert from 'node:assert/strict';
import {actionLocators, actionLogLines} from '../bridge/action-logging.mjs';
import {SecretGuard} from '../bridge/security.mjs';
import {Runtime} from '../bridge/runtime.mjs';
import {assertionRequest, assertionResolved, assertionFailure} from '../bridge/assertion-logging.mjs';

const guard = new SecretGuard([]);

test('extracts the exact action receiver from fork-generated code', () => {
  const code = "await page.locator('#slCode').fill('private-value');";
  assert.deepEqual(actionLocators(code), ["page.locator('#slCode')"]);
  assert.deepEqual(actionLogLines('browser_type','page1',code,undefined,
    {text:'private-value'},guard,true), [
    '[ACTION] fill', '[PAGE] page1', "[LOCATOR] page.locator('#slCode')", '[VALUE] <masked>']);
});

test('disabled locator logging omits the locator while masking fill values', () => {
  const lines = actionLogLines('browser_type','page1',
    "await page.locator('#password').fill('private-value');",undefined,
    {text:'private-value'},guard,false);
  assert.equal(lines.some(line => line.startsWith('[LOCATOR]')),false);
  assert.equal(lines.includes('[VALUE] <masked>'),true);
  assert.equal(lines.join('\n').includes('private-value'),false);
});

test('role fallback is logged verbatim, and sensitive selector values are redacted', () => {
  const role = "page.getByRole('tab', { name: 'SL Codes' })";
  assert.deepEqual(actionLocators(`await ${role}.click();`),[role]);
  assert.ok(actionLogLines('browser_click','page1',`await ${role}.click();`,undefined,
    {},guard,true).includes(`[LOCATOR] ${role}`));
  const sensitive = "await page.locator('[data-session-id=\"opaque-secret\"]').click();";
  assert.ok(actionLogLines('browser_click','page1',sensitive,undefined,{},guard,true)
    .includes('[LOCATOR] <redacted>'));
});

test('URL assertion has no locator to report', () => {
  const lines = actionLogLines('browser_assert','page1','',undefined,
    {kind:'url',expected:'https://example.test/'},guard,true);
  assert.equal(lines.some(line => line.startsWith('[LOCATOR]')),false);
});

test('wait actions report a real locator when the fork used one', () => {
  const locator = "page.getByText('Loaded')";
  const lines = actionLogLines('browser_wait_for','page1',`await ${locator}.waitFor();`,
    undefined,{},guard,true);
  assert.ok(lines.includes(`[LOCATOR] ${locator}`));
});

test('state URL omits query strings and masks token path segments', () => {
  const runtime = new Runtime({});
  const page = {url:() => 'https://example.test/access-token/opaque-token?session=hidden',
    isClosed:() => false};
  runtime.pages.set(page,{alias:'page',page,opener:null});
  runtime.active = page;
  const line = runtime.stateLine();
  assert.match(line,/\[STATE\] v0 page=page url=https:\/\/example\.test\/access-token\/\[REDACTED\]/);
  assert.equal(line.includes('opaque-token'),false);
  assert.equal(line.includes('hidden'),false);
});

test('assertion diagnostics include request, resolved locator, and actual UI state', () => {
  const args = {kind:'visible',target:'e137',
    targetDescriptor:{role:'tab',name:'Commodity Subtypes'}};
  const request = assertionRequest(args,'page1',[],guard);
  assert.match(request,/\[ASSERT_REQUEST\]/);
  assert.match(request,/- target: e137/);
  assert.match(request,/- targetDescriptor: \{"role":"tab","name":"Commodity Subtypes"\}/);
  assert.match(request,/- page alias: page1/);
  const locator = "page.getByRole('tab', { name: 'Commodity Subtypes' })";
  assert.match(assertionResolved(locator,[],guard),/actual locator used: page\.getByRole/);
  const failure = assertionFailure(args,locator,
    {count:1,visible:false,text:'Commodity Subtypes',selected:'false'},'Expected result assertion failed',[],guard);
  assert.match(failure,/actual visible: false/);
  assert.match(failure,/actual text: Commodity Subtypes/);
  assert.match(failure,/actual selected: false/);
});

test('assertion diagnostics mask form values and secret-bearing selectors', () => {
  const args = {kind:'value',target:'input[value="private-value"]',expected:'private-value',
    targetDescriptor:{selector:'input[value="private-value"]'}};
  const request = assertionRequest(args,'page1',['private-value'],guard);
  const resolved = assertionResolved("page.locator('input[value=\"private-value\"]')",
    ['private-value'],guard);
  const failure = assertionFailure(args,"page.locator('#password')",
    {visible:true,text:'private-value',selected:null},undefined,['private-value'],guard);
  for (const logged of [request,resolved,failure]) assert.equal(logged.includes('private-value'),false);
  assert.match(request,/expected: <masked>/);
  assert.match(resolved,/actual locator used: <redacted>/);
  assert.match(failure,/actual text: <masked>/);
});
