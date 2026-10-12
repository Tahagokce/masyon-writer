const SENSITIVE_SELECTOR = /\[[^\]]*(?:value|token|secret|session|auth|credential|api[_-]?key|cookie)\s*=/i;

function safeString(value, inputValues, guard) {
  if (SENSITIVE_SELECTOR.test(value)) return '<redacted>';
  let text = value;
  for (const input of [...new Set(inputValues)].filter(Boolean).sort((a, b) => b.length - a.length))
    text = text.split(input).join('<masked>');
  return guard.clean(text).replace(/\r/g, '\\r').replace(/\n/g, '\\n');
}

function safeField(value, inputValues, guard) {
  if (value === undefined || value === null) return '<none>';
  if (typeof value === 'string') return safeString(value, inputValues, guard);
  if (Array.isArray(value)) return value.map(item => safeField(item, inputValues, guard));
  if (typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .map(([key, item]) => [key, safeField(item, inputValues, guard)]));
  return value;
}

function printed(value) { return typeof value === 'object' ? JSON.stringify(value) : String(value); }

export function assertionRequest(args, pageAlias, inputValues, guard) {
  const expected = args.kind === 'value' ? '<masked>' : safeField(args.expected, inputValues, guard);
  return ['[ASSERT_REQUEST]', `- kind: ${printed(safeField(args.kind, inputValues, guard))}`,
    `- target: ${printed(safeField(args.target, inputValues, guard))}`,
    `- targetDescriptor: ${printed(safeField(args.targetDescriptor, inputValues, guard))}`,
    `- expected: ${printed(expected)}`, `- page alias: ${pageAlias || 'unknown'}`].join('\n');
}

export function assertionResolved(locator, inputValues, guard) {
  return `[ASSERT_RESOLVED]\n- actual locator used: ${printed(safeField(locator, inputValues, guard))}`;
}

export function assertionFailure(args, locator, actual, reason, inputValues, guard) {
  const expected = args.kind === 'value' ? '<masked>' : safeField(args.expected, inputValues, guard);
  const text = actual?.text === undefined ? '<unavailable>' :
    safeString(String(actual.text), inputValues, guard).slice(0, 300);
  const fields = ['[ASSERT_FAILURE]', `- assertion kind: ${printed(safeField(args.kind, inputValues, guard))}`,
    `- expected: ${printed(expected)}`,
    `- resolved locator: ${printed(safeField(locator, inputValues, guard))}`,
    `- actual visible: ${actual?.visible ?? '<unavailable>'}`,
    `- actual text: ${text}`,
    `- actual selected: ${actual?.selected ?? '<unavailable>'}`];
  if (actual?.count !== undefined) fields.push(`- actual count: ${actual.count}`);
  if (actual?.url !== undefined) fields.push(`- actual url: ${safeString(actual.url, inputValues, guard)}`);
  if (reason) fields.push(`- reason: ${safeString(String(reason), inputValues, guard).slice(0, 300)}`);
  return fields.join('\n');
}
