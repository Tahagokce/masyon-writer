import crypto from 'node:crypto';

export const BATCH_TOOLS = ['browser_type','browser_fill_form','browser_select_option',
  'browser_press_key','browser_click'];

// This stays inside the bridge. No DOM or fingerprint is sent to Gemini/logs.
export async function screenStamp(runtime) {
  if (!runtime.active || runtime.active.isClosed() || runtime.dialog) return null;
  try {
    const content = await runtime.active.evaluate(() => {
      const visible = e => Boolean(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
      return JSON.stringify({document:performance.timeOrigin, url:location.href,
        text:document.body?.innerText,
        controls:[...document.querySelectorAll('input,button,select,a,[role],.ant-modal,.modal')]
          .filter(visible).map(e => [e.tagName,e.id,e.getAttribute('role'),e.getAttribute('aria-expanded'),
            e.getAttribute('aria-invalid'),e.hasAttribute('disabled')])});
    });
    return crypto.createHash('sha256').update(content).update(JSON.stringify(runtime.pageState()))
      .update(String(runtime.navigationVersion)).digest('hex');
  } catch { return null; }
}

export async function executeBatch(runtime, args) {
  if (!Array.isArray(args.actions) || args.actions.length < 1 || args.actions.length > 5)
    throw new Error('A deterministic batch needs 1–5 actions');
  for (const action of args.actions) {
    if (!BATCH_TOOLS.includes(action.tool) || !action.arguments || typeof action.arguments !== 'object')
      throw new Error('Only fill, select, press and click operations are allowed in a batch');
  }
  const actions = args.actions.flatMap(action => action.tool === 'browser_fill_form' && Array.isArray(action.arguments.fields)
    ? action.arguments.fields.map(field => ({tool:action.tool,arguments:{fields:[field]}})) : [action]);
  if (actions.length < 1 || actions.length > 5) throw new Error('A batch allows at most five fields/actions');
  const results = [];
  let checkpoint = false;
  for (const action of actions) {
    const before = await screenStamp(runtime);
    if (!before) { checkpoint = true; break; }
    let result;
    try { result = await runtime.call(action.tool, action.arguments); }
    catch (error) {
      result = {isError:true, executed:false, mutating:true,
        ...runtime.guard.diagnostic(error, action.tool, runtime.pages.get(runtime.active)?.alias),
        logEvents:runtime.drainLogEvents()};
    }
    results.push({tool:action.tool, ...result});
    const after = await screenStamp(runtime);
    // Click/Enter/submit always ends a batch, even before asynchronous changes
    // become visible. Do not batch an uncertain UI transition.
    checkpoint = result.isError || before !== after || result.transition ||
      action.tool === 'browser_click' || action.tool === 'browser_press_key' || action.arguments.submit === true;
    if (checkpoint) break;
  }
  const last = results.at(-1) || {};
  return {isError:results.some(r => r.isError), error:last.error, errorClass:last.errorClass,
    failedTool:last.isError?last.tool:undefined, modalState:last.modalState,
    executed:results.some(r => r.executed), mutating:results.some(r => r.mutating),
    results:results.map(({tool,isError,error,errorClass,executed}) => ({tool,isError,error,errorClass,executed})),
    logEvents:results.flatMap(result => result.logEvents || []),
    batchExecuted:results.length, remainingActions:actions.length-results.length,
    transition:checkpoint, checkpointRequired:checkpoint, activePage:runtime.pages.get(runtime.active)?.alias,
    pages:runtime.pageState(), metrics:runtime.metrics()};
}
