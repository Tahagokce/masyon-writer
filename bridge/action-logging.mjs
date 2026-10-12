import ts from 'typescript';

const ACTION_METHODS = new Set(['click','dblclick','fill','press','type','selectOption',
  'hover','dragTo','check','uncheck','focus','blur','setInputFiles','waitFor']);
const LOCATOR_START = /^page\.(?:locator|getBy\w+|frameLocator)\(/;
const ACTION_NAMES = {browser_click:'click',browser_type:'fill',browser_fill_form:'fill',
  browser_select_option:'select',browser_press_key:'press',browser_hover:'hover',
  browser_drag:'drag',browser_navigate:'navigate',browser_navigate_back:'navigate back',
  browser_navigate_forward:'navigate forward',browser_reload:'reload',
  browser_wait_for:'wait',
  browser_assert:'assert',browser_handle_dialog:'dialog',browser_new_page:'new page',
  browser_close_page:'close page',browser_switch_page:'switch page'};

// Slice the actual code returned by the fork. Never inspect call arguments such as
// fill values; the receiver of the action call is the locator used for that call.
export function actionLocators(code) {
  if (!code) return [];
  const source = ts.createSourceFile('fork-action.ts', code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  if (source.parseDiagnostics.length) return [];
  const found = [];
  function visit(node) {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) &&
        ACTION_METHODS.has(node.expression.name.text)) {
      const receiver = node.expression.expression.getText(source);
      if (LOCATOR_START.test(receiver)) found.push(receiver);
      if (node.expression.name.text === 'dragTo' && node.arguments.length) {
        const destination = node.arguments[0].getText(source);
        if (LOCATOR_START.test(destination)) found.push(destination);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  return [...new Set(found)];
}

export function actionName(tool) { return ACTION_NAMES[tool] || tool.replace(/^browser_/, ''); }

export function actionLogLines(tool, alias, code, resolvedLocator, args, guard, logLocators) {
  const lines = [`[ACTION] ${actionName(tool)}`, `[PAGE] ${alias}`];
  if (logLocators) {
    const locators = resolvedLocator ? [resolvedLocator] : actionLocators(code);
    const fillValues = tool === 'browser_type' ? [args.text] :
      tool === 'browser_fill_form' ? (args.fields || []).map(field => field.value) : [];
    for (const locator of locators) {
      // If a selector itself contains a secret, an exact log is unsafe.
      const containsFillValue = fillValues.some(value => typeof value === 'string' &&
        value.length > 1 && locator.includes(value));
      const sensitiveAttribute = /\[\s*[^\]]*(?:token|secret|session|auth|credential|api[_-]?key|cookie)[^\]]*=/i.test(locator);
      lines.push(`[LOCATOR] ${containsFillValue || sensitiveAttribute || guard.clean(locator) !== locator ? '<redacted>' : locator}`);
    }
    if (!locators.length && !(tool === 'browser_assert' && args.kind === 'url') &&
      ['browser_click','browser_type','browser_fill_form','browser_select_option',
        'browser_hover','browser_drag','browser_assert'].includes(tool)) lines.push('[LOCATOR] <unavailable>');
  }
  if (['browser_type','browser_fill_form'].includes(tool)) lines.push('[VALUE] <masked>');
  return lines;
}
