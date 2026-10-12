import {uiInventory} from './ui-inventory.mjs';
import {resolveLocator} from './locator-resolver.mjs';
import {debugSnapshot} from './snapshot.mjs';
import {assertionRequest, assertionResolved} from './assertion-logging.mjs';

const ACTIONS = new Set(['click','fill','select','check','uncheck','press','navigate','assert']);
const ASSERTIONS = new Set(['visible','hidden','text','value','enabled','disabled',
  'checked','unchecked','selected','unselected','url']);
const pause = ms => new Promise(resolve => setTimeout(resolve,ms));

function emit(runtime, line) {
  if (line.startsWith('[LOCATOR]') && !runtime.logLocators) return;
  runtime.emit(line);
}

function safeTarget(runtime, target, inputValues) {
  return JSON.parse(debugSnapshot(target || {},inputValues,runtime.guard));
}

function actionValue(runtime, value) {
  if (typeof value !== 'string') throw new Error('Semantic action value must be a string');
  if (Object.hasOwn(runtime.guard.values,value))
    return {actual:runtime.guard.values[value],code:`process.env[${JSON.stringify(value)}]!`};
  return {actual:value,code:JSON.stringify(value)};
}

function resultForDecision(runtime, status, candidates, inputValues) {
  const safe = JSON.parse(debugSnapshot(candidates.map(({role,name,id,testId}) =>
    ({role,name,id,testId})).slice(0,8),inputValues,runtime.guard));
  const reason = status === 'ambiguous'
    ? 'AMBIGUOUS_TARGET: Multiple UI elements match the semantic target; refine the role/name or inspect once.'
    : 'UNRESOLVED_TARGET: No unique actionable UI element matches; refine the target or inspect once.';
  emit(runtime,`[RESOLVE] ${status} candidates=${safe.length}`);
  return {isError:false,denied:true,needsDecision:true,reason,
    resolverStatus:status === 'ambiguous' ? 'AMBIGUOUS' : 'NO_MATCH',
    candidates:safe,executed:false};
}

async function selected(locator) {
  const aria = await locator.getAttribute('aria-selected');
  if (aria !== null) return aria === 'true';
  return await locator.getAttribute('selected') !== null;
}

async function assertionPasses(page, locator, kind, expected) {
  switch (kind) {
    case 'visible': return locator.isVisible();
    case 'hidden': return !(await locator.isVisible());
    case 'text': return (await locator.innerText()).includes(expected);
    case 'value': return (await locator.inputValue()) === expected;
    case 'enabled': return locator.isEnabled();
    case 'disabled': return !(await locator.isEnabled());
    case 'checked': return locator.isChecked();
    case 'unchecked': return !(await locator.isChecked());
    case 'selected': return selected(locator);
    case 'unselected': return !(await selected(locator));
    case 'url': return page.url() === expected;
    default: throw new Error('Unsupported semantic assertion kind');
  }
}

function assertionCode(expression, kind, expected) {
  const code = JSON.stringify(expected ?? '');
  const checks = {visible:`expect(${expression}).toBeVisible()`,
    hidden:`expect(${expression}).toBeHidden()`,text:`expect(${expression}).toContainText(${code})`,
    value:`expect(${expression}).toHaveValue(${code})`,enabled:`expect(${expression}).toBeEnabled()`,
    disabled:`expect(${expression}).toBeDisabled()`,checked:`expect(${expression}).toBeChecked()`,
    unchecked:`expect(${expression}).not.toBeChecked()`,
    selected:`expect(${expression}).toHaveAttribute('aria-selected', 'true')`,
    unselected:`expect(${expression}).not.toHaveAttribute('aria-selected', 'true')`,
    url:`expect(page).toHaveURL(${code})`};
  return `await ${checks[kind]};`;
}

export async function executeSemanticAction(runtime, action, entry) {
  if (!action || !ACTIONS.has(action.type)) throw new Error('Unsupported semantic action type');
  if (action.type === 'assert' && !ASSERTIONS.has(action.kind)) throw new Error('Unsupported semantic assertion kind');
  const inputValues = await runtime.currentFormValues().catch(() => []);
  const target = safeTarget(runtime,action.target,inputValues);
  emit(runtime,`[PLAN] action=${action.type} target=${JSON.stringify(target.name || target.label || target.text || '')} role=${target.roleHint || ''}`);
  const isUrl = action.type === 'assert' && action.kind === 'url';
  const isPagePress = action.type === 'press' && !action.target;
  const isNavigate = action.type === 'navigate';
  let inventory = [], resolved;
  if (!isUrl && !isPagePress && !isNavigate) {
    const includeHidden = action.type === 'assert' && action.kind === 'hidden';
    const deadline = action.type === 'assert' ? Date.now() + 5000 : 0;
    do {
      inventory = await uiInventory(runtime.active,{includeHidden});
      resolved = await resolveLocator(runtime.active,action.target,inventory,
        {includeDisabled:action.type === 'assert',includeHidden});
      if (!['missing','unresolved'].includes(resolved.status) || Date.now() >= deadline) break;
      await pause(100);
    } while (true);
    emit(runtime,`[INVENTORY] candidateCount=${inventory.length}`);
    if (resolved.status !== 'resolved') return {...resultForDecision(runtime,resolved.status,
      resolved.candidates || [],inputValues),inventoryCount:inventory.length};
    if (action.type === 'assert' && resolved.candidate.role === 'dialog' && resolved.candidate.visible)
      emit(runtime,'[UI] dialog detected');
    const item = JSON.parse(debugSnapshot({role:resolved.candidate.role,name:resolved.candidate.name,
      id:resolved.candidate.id},inputValues,runtime.guard));
    emit(runtime,`[RESOLVE] matched role=${item.role} name=${JSON.stringify(item.name)} id=${JSON.stringify(item.id)}`);
    const safeLocator = debugSnapshot(resolved.expression,inputValues,runtime.guard);
    emit(runtime,`[LOCATOR] ${safeLocator}`);
  }
  const expression = resolved?.expression;
  const locator = resolved?.locator;
  let code = '';
  if (action.type === 'assert') {
    if (['text','value','url'].includes(action.kind) && typeof action.expected !== 'string')
      throw new Error('Semantic assertion needs expected string');
    const expected = action.kind === 'value' ? actionValue(runtime,action.expected).actual : action.expected;
    emit(runtime,assertionRequest({kind:action.kind,targetDescriptor:action.target,expected:action.expected},
      runtime.pages.get(runtime.active)?.alias,inputValues,runtime.guard));
    emit(runtime,assertionResolved(expression,inputValues,runtime.guard));
    code = assertionCode(expression,action.kind,action.kind === 'value' &&
      Object.hasOwn(runtime.guard.values,action.expected) ? undefined : action.expected);
    if (action.kind === 'value' && Object.hasOwn(runtime.guard.values,action.expected))
      code = `await expect(${expression}).toHaveValue(process.env[${JSON.stringify(action.expected)}]!);`;
    entry.code = runtime.guard.assertCode(code);
    const deadline = Date.now() + 5000;
    let verified = false;
    while (true) {
      try { verified = await assertionPasses(runtime.active,locator,action.kind,expected); }
      catch { verified = false; }
      if (verified || Date.now() >= deadline) break;
      await pause(100);
    }
    if (!verified) {
      await runtime.logAssertionFailure({kind:action.kind,expected:action.expected},expression,
        'Expected result assertion failed',inputValues);
      return {isError:true,errorClass:'AssertionError',error:'Expected result assertion failed',
        'ran playwright code':code,resolvedLocator:expression,inventoryCount:inventory.length};
    }
    const evidenceId = `assertion-${++runtime.evidence}`;
    emit(runtime,`[ASSERT] ${action.kind} verified evidence=${evidenceId}`);
    return {isError:false,executed:true,evidenceId,
      assertion:{kind:action.kind,targetDescriptor:action.target,expected:action.expected},
      'ran playwright code':code,resolvedLocator:expression,inventoryCount:inventory.length};
  }
  if (action.type === 'click') {
    code = `await ${expression}.click();`;
    entry.code = runtime.guard.assertCode(code);
    await locator.click();
  } else if (action.type === 'fill') {
    if (resolved.candidate.inputType === 'password' &&
        !Object.hasOwn(runtime.guard.values,action.value))
      throw new Error('Password fields require a configured credential environment variable name');
    const value = actionValue(runtime,action.value);
    code = `await ${expression}.fill(${value.code});`;
    entry.code = runtime.guard.assertCode(code);
    await locator.fill(value.actual);
  } else if (action.type === 'select') {
    if (resolved.candidate.tag !== 'select')
      return {...resultForDecision(runtime,'unresolved',[resolved.candidate],inputValues),inventoryCount:inventory.length};
    const value = actionValue(runtime,action.value);
    const options = await locator.locator('option').evaluateAll(nodes => nodes.map(node =>
      ({value:node.value,label:(node.label || node.textContent || '').trim()})));
    const byLabel = options.filter(option => option.label === value.actual);
    const byValue = options.filter(option => option.value === value.actual);
    if (byLabel.length !== 1 && byValue.length !== 1)
      return {...resultForDecision(runtime,'unresolved',[],inputValues),inventoryCount:inventory.length};
    const mode = byLabel.length === 1 ? 'label' : 'value';
    code = `await ${expression}.selectOption({ ${mode}: ${value.code} });`;
    entry.code = runtime.guard.assertCode(code);
    await locator.selectOption({[mode]:value.actual});
  } else if (action.type === 'check' || action.type === 'uncheck') {
    code = `await ${expression}.${action.type}();`;
    entry.code = runtime.guard.assertCode(code);
    await locator[action.type]();
  } else if (action.type === 'press') {
    const key = action.key || action.value;
    if (typeof key !== 'string' || !key) throw new Error('Semantic press needs a key');
    code = isPagePress ? `await page.keyboard.press(${JSON.stringify(key)});`
      : `await ${expression}.press(${JSON.stringify(key)});`;
    entry.code = runtime.guard.assertCode(code);
    if (isPagePress) await runtime.active.keyboard.press(key);
    else await locator.press(key);
  } else if (action.type === 'navigate') {
    const url = new URL(action.url);
    if ((!['http:','https:'].includes(url.protocol) && action.url !== 'about:blank') ||
        url.username || url.password) throw new Error('Navigation requires HTTP(S) without embedded credentials');
    code = `await page.goto(${JSON.stringify(action.url)});`;
    entry.code = runtime.guard.assertCode(code);
    await runtime.active.goto(action.url);
  }
  emit(runtime,`[ACTION] ${action.type} successful`);
  return {isError:false,executed:true,'ran playwright code':code,
    resolvedLocator:expression,inventoryCount:inventory.length};
}

export async function executeSemanticPlan(runtime, args) {
  if (!Array.isArray(args.actions) || !args.actions.length || args.actions.length > 10)
    throw new Error('Semantic plan requires 1–10 actions');
  const results = [], events = [];
  for (const action of args.actions) {
    const result = await runtime.call('browser_semantic_action',{action});
    events.push(...(result.logEvents || []));
    results.push(result);
    if (result.isError || result.needsDecision) break;
  }
  const last = results.at(-1);
  return {isError:Boolean(last.isError),error:last.error,errorClass:last.errorClass,
    denied:Boolean(last.denied),needsDecision:Boolean(last.needsDecision),reason:last.reason,
    resolverStatus:last.resolverStatus,
    candidates:last.candidates,executed:results.some(result => result.executed),
    mutating:results.some(result => result.mutating),transition:results.some(result => result.transition),
    checkpointRequired:results.some(result => result.transition),
    evidenceId:results.findLast(result => result.evidenceId)?.evidenceId,
    evidenceIds:results.map(result => result.evidenceId).filter(Boolean),
    assertions:results.map(result => result.assertion).filter(Boolean),
    batchExecuted:results.length,remainingActions:args.actions.length-results.length,
    results:results.map(result => ({isError:result.isError,executed:result.executed,
      evidenceId:result.evidenceId,error:result.error,needsDecision:result.needsDecision})),
    activePage:runtime.pages.get(runtime.active)?.alias,pages:runtime.pageState(),metrics:runtime.metrics(),
    logEvents:events};
}
