import test from 'node:test';
import assert from 'node:assert/strict';
import { Journal, rewritePage } from '../bridge/recording.mjs';
import { SecretGuard } from '../bridge/security.mjs';
import { loadFork } from '../bridge/runtime.mjs';

test('page rewriting preserves literal values', () => {
  const code = rewritePage("await page.getByText('page').click();", 'page2');
  assert.match(code, /page2.getByText/);
  assert.match(code, /['"]page['"]/);
});
test('recording binds popup before use and listener before action', () => {
  const journal = new Journal(); journal.start();
  const first = journal.begin('page','browser_click',true);
  first.code = "await page.locator('#open').click();";
  first.pages.push({alias:'page1',opener:'page'});
  journal.begin('page1','browser_type',true).code = "await page.locator('#code').fill('AUTO_1');";
  const code = journal.render('SUCCESS','');
  assert.ok(code.indexOf("context.on('page'") < code.indexOf("locator('#open')"));
  assert.ok(code.indexOf('const page1 =') < code.indexOf('page1.locator'));
  assert.match(code, /expect\(await page1.opener\(\)\).toBe\(page\)/);
});
test('partial execution emits a failing test', () => {
  const journal = new Journal(); journal.start();
  assert.match(journal.render('BLOCKED','ambiguous business intent'), /throw new Error\("BLOCKED:/);
});
test('incomplete recording preserves earlier actions and cannot pass', () => {
  const journal = new Journal(); journal.start();
  journal.begin('page','browser_click',true).code = "await page.locator('#save').click();";
  journal.problem = 'Unattributed page';
  const code = journal.render('SUCCESS','');
  assert.match(code, /locator\('#save'\)/);
  assert.match(code, /BLOCKED: partial recording/);
});
test('raw and escaped secrets cannot be exported', () => {
  process.env.POC_TEST_SECRET = 'secret"quote&special';
  const guard = new SecretGuard(['POC_TEST_SECRET']);
  for (const value of guard.variants) assert.throws(() => guard.assertCode(`fill('${value}')`));
  assert.equal(guard.assertCode("fill(process.env.WBS_PASSWORD!)"), "fill(process.env.WBS_PASSWORD!)");
  assert.throws(() => guard.assertCode("await page.goto('https://example.test/?token=unknown-sensitive-token');"));
  delete process.env.POC_TEST_SECRET;
});
test('missing fork never resolves from node_modules', () => {
  assert.throws(() => loadFork(''), /required/);
  assert.throws(() => loadFork('/tmp/does-not-exist-fork'));
});
