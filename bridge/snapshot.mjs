// Scope selection is inspection only. Never let a non-unique helper locator
// turn a recoverable inspection into a failed business action.
const MODAL_SCOPES = ['[role="dialog"]:visible', 'nz-modal-container:visible',
  '.ant-modal:visible', '[aria-modal="true"]:visible', '.modal:visible', 'dialog[open]:visible'];

// Keep the full MCP tree for diagnostics, but never expose live form values or
// secret-bearing fields in a DEBUG log. This does not alter the returned snapshot.
export function debugSnapshot(snapshot, inputValues, guard) {
  const values = [...new Set(inputValues.filter(value => typeof value === 'string' && value))]
    .sort((a, b) => b.length - a.length);
  const sensitiveKey = /^(?:value|inputValue|password|authorization|cookie|.*(?:token|secret|session|api[_-]?key|credential).*)$/i;
  function scrub(node, key = '') {
    if (sensitiveKey.test(key)) return '<masked>';
    if (typeof node === 'string') {
      let text = node;
      for (const value of values) text = text.split(value).join('<masked>');
      return guard.clean(text);
    }
    if (Array.isArray(node)) return node.map(item => scrub(item));
    if (node && typeof node === 'object')
      return Object.fromEntries(Object.entries(node).map(([field, value]) => [field, scrub(value, field)]));
    return node;
  }
  const sanitized = scrub(snapshot);
  return typeof sanitized === 'string' ? sanitized : JSON.stringify(sanitized, null, 2);
}

export async function modalScope(page, notices) {
  for (const selector of MODAL_SCOPES) {
    const candidates = page.locator(selector);
    const count = await candidates.count();
    if (!count) continue;
    if (count > 1) notices.push('[SNAPSHOT] Multiple dialog candidates found; using last visible dialog');
    return candidates.last();
  }
  return null;
}

export async function inspectSnapshot(runtime, args) {
  runtime.snapshotCount++;
  const notices = [];
  let scoped = false;
  const options = {depth: args.depth ?? 5};
  try {
    let locator;
    if (args.target) {
      const tab = runtime.fork.tools.Tab.forPage(runtime.active);
      locator = (await tab.targetLocator({target:args.target})).locator.filter({visible:true});
      const count = await locator.count();
      if (count !== 1) {
        notices.push(count > 1 ? '[SNAPSHOT] Multiple scope candidates found' : '[SNAPSHOT] Scope is no longer visible');
        throw new Error('Snapshot scope is not unique');
      }
    } else if (args.scope !== 'page') {
      locator = await modalScope(runtime.active, notices);
    }
    if (locator) { options.target = locator.toString(); scoped = true; }
    const result = await runtime.raw('browser_snapshot', options);
    if (!result.isError) {
      runtime.capturedSnapshot?.();
      return {...result, notices, snapshotScope:scoped?'target':'page'};
    }
    if (!scoped) return inspectionFailure(runtime, result, notices);
  } catch (error) {
    if (runtime.active?.isClosed()) throw error;
    // Scope may disappear between count() and the strict MCP snapshot call.
  }
  runtime.snapshotFallbackCount++;
  notices.push('[SNAPSHOT] Falling back to whole-page snapshot');
  try {
    const fallback = await runtime.raw('browser_snapshot', {depth:args.depth ?? 5});
    if (fallback.isError) return inspectionFailure(runtime, fallback, notices);
    runtime.capturedSnapshot?.();
    return {...fallback, notices, snapshotScope:'page', snapshotFallback:true};
  } catch (error) { return inspectionFailure(runtime, {error:error.message, errorClass:error.name}, notices); }
}

function inspectionFailure(runtime, result, notices) {
  const impossible = !runtime.active || runtime.active.isClosed() || !runtime.browser.isConnected();
  return {...result, isError:true, recoverable:!impossible, notices,
    errorClass:result.errorClass || 'SnapshotError', snapshot:undefined,
    error:result.error || 'Snapshot unavailable; inspect another target or retry after the UI settles'};
}

export function compactSnapshot(snapshot, limit = 4000) {
  if (typeof snapshot === 'string') {
    try { snapshot = JSON.parse(snapshot); }
    catch { return {snapshot:snapshot.slice(0,limit), snapshotTruncated:snapshot.length>limit}; }
  }
  if (!snapshot) return {};
  let budget = limit - 64, truncated = false;
  function visit(node) {
    if (typeof node === 'string') {
      const text = node.slice(0,240);
      truncated ||= text.length < node.length;
      const cost = JSON.stringify(text).length + 2;
      if (cost > budget) { truncated = true; return undefined; }
      budget -= cost; return text;
    }
    if (Array.isArray(node)) return node.map(visit).filter(v => v !== undefined);
    if (!node || typeof node !== 'object') return undefined;
    if (node.ariaHidden) return undefined;
    const item = {};
    for (const key of ['role','name','text','ref','disabled','checked','selected','expanded','value','level','invalid','pressed','placeholder','active']) {
      if (node[key] !== undefined) {
        item[key] = typeof node[key] === 'string' ? node[key].slice(0,240) : node[key];
        truncated ||= typeof node[key] === 'string' && node[key].length > 240;
      }
    }
    const cost = JSON.stringify(item).length + 20;
    if (cost > budget) { truncated = true; return undefined; }
    budget -= cost;
    if (node.children) item.children = visit(node.children);
    return item;
  }
  const result = visit(snapshot);
  return {snapshot:JSON.stringify(result ?? []), snapshotTruncated:truncated};
}
