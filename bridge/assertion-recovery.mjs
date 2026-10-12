// Snapshot refs expire, so retain only small semantic descriptions for assertions.
const REF = /^(?:f\d+)?e\d+$/;
const STALE = /Ref\s+((?:f\d+)?e\d+)\s+not found in the current page snapshot/i;
const FIELDS = ['id', 'role', 'name', 'label', 'text', 'selector'];

export function staleRef(error) {
  return String(error?.error || error?.message || error || '').match(STALE)?.[1];
}

export function descriptor(value) {
  if (!value || typeof value !== 'object') return {};
  return Object.fromEntries(FIELDS.filter(key => typeof value[key] === 'string' && value[key].trim())
    .map(key => [key, value[key].slice(0, 240)]));
}

export function hasUsableDescriptor(value) {
  const data = descriptor(value);
  return Boolean(data.id || data.selector || data.label || data.text || data.role);
}

export function snapshotNodes(snapshot) {
  if (typeof snapshot === 'string') {
    try { snapshot = JSON.parse(snapshot); } catch { return []; }
  }
  const nodes = [];
  function visit(node) {
    if (Array.isArray(node)) return node.forEach(visit);
    if (!node || typeof node !== 'object') return;
    if (typeof node.ref === 'string' && REF.test(node.ref)) nodes.push(node);
    if (node.children) visit(node.children);
  }
  visit(snapshot);
  return nodes;
}

export function rememberSnapshotRefs(cache, pageAlias, snapshot) {
  for (const node of snapshotNodes(snapshot)) {
    const semantic = descriptor(node);
    if (Object.keys(semantic).length) cache.set(`${pageAlias}:${node.ref}`, semantic);
  }
  // Keep recent refs for a changed page without retaining whole snapshots.
  while (cache.size > 2000) cache.delete(cache.keys().next().value);
}

async function unique(locator) {
  try { return await locator.count() === 1; } catch { return false; }
}

export async function resolveAssertionTarget(page, snapshot, semantic) {
  const nodes = snapshotNodes(snapshot);
  const data = descriptor(semantic);
  const idTarget = async id => `#${await page.evaluate(value => CSS.escape(value), id)}`;
  if (data.id) {
    const target = await idTarget(data.id);
    if (await unique(page.locator(target))) return {target, targetDescriptor:data};
  }

  const match = predicate => {
    const found = nodes.filter(node => node.ref && predicate(node));
    return found.length === 1 ? found[0] : undefined;
  };
  let node;
  if (data.role && data.name)
    node = match(item => item.role === data.role && item.name === data.name);
  if (!node && data.role && !data.name)
    node = match(item => item.role === data.role);
  if (!node && data.label)
    node = match(item => item.name === data.label || item.label === data.label);
  if (!node && data.text)
    node = match(item => item.text === data.text || item.name === data.text);
  if (node) {
    const resolved = {...data, ...descriptor(node)};
    if (resolved.id) {
      const target = await idTarget(resolved.id);
      if (await unique(page.locator(target))) return {target, targetDescriptor:resolved};
    }
    return {target:node.ref, targetDescriptor:resolved};
  }
  if (data.selector && await unique(page.locator(data.selector)))
    return {target:data.selector, targetDescriptor:data};
  // Reused ref numbers alone do not establish that this is the same element.
  return null;
}
