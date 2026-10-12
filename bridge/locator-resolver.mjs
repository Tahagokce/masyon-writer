export function normalized(value) {
  return String(value || '').normalize('NFKD').replace(/\p{M}/gu, '')
    .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}

export function stableId(id) {
  if (!id || /^\d+$/.test(id) || /\d{4,}$/.test(id)) return false;
  return !/^(?:cdk-overlay-\d+|nz-tabs-\d+-tab-\d+|react-select-\d+|mui-\d+|ember\d+|auto[-_]?generated[-_]?\d+|:r[\w-]+:)$/i.test(id);
}

const COMPATIBLE = {input:['textbox','spinbutton','combobox'],textbox:['textbox','searchbox'],
  select:['combobox','listbox'],combobox:['combobox','listbox'],button:['button'],
  tab:['tab'],tabpanel:['tabpanel'],link:['link'],checkbox:['checkbox'],radio:['radio'],
  option:['option'],menuitem:['menuitem','menuitemcheckbox','menuitemradio']};

function roleMatches(hint, role) {
  if (!hint) return true;
  const key = normalized(hint).replace(/ /g, '');
  return role === key || (COMPATIBLE[key] || []).includes(role);
}

function score(target, element) {
  if (!roleMatches(target.roleHint, element.role)) return -1;
  const queries = [target.name,target.text,target.label,target.placeholder].filter(Boolean);
  if (!queries.length) return target.roleHint ? 20 : -1;
  let best = -1;
  const comparisons = [
    [target.name,element.name,100], [target.name,element.text,90],
    [target.text,element.text,90], [target.label,element.label,80],
    [target.name,element.label,80], [target.placeholder,element.placeholder,70],
    [target.name,element.placeholder,70],
  ];
  for (const [query, actual, weight] of comparisons) {
    if (!query || !actual) continue;
    if (query === actual) best = Math.max(best,weight);
    else if (normalized(query) && normalized(query) === normalized(actual)) best = Math.max(best,weight-40);
  }
  return best < 0 ? -1 : best + (target.roleHint ? 20 : 0);
}

export function chooseCandidate(target, inventory, {includeDisabled = false, includeHidden = false} = {}) {
  if (!target || typeof target !== 'object' ||
      ![target.name,target.roleHint,target.label,target.text,target.placeholder].some(Boolean))
    return {status:'missing', candidates:[]};
  const ranked = inventory.filter(item => (includeHidden || item.visible) && (includeDisabled || item.enabled))
    .map(item => ({item,score:score(target,item)})).filter(entry => entry.score >= 0)
    .sort((a,b) => b.score-a.score);
  if (!ranked.length) return {status:'missing', candidates:[]};
  const strongest = ranked.filter(entry => entry.score === ranked[0].score);
  if (strongest.length > 1) return {status:'ambiguous',candidates:strongest.map(entry => entry.item)};
  return {status:'resolved',candidate:ranked[0].item,score:ranked[0].score};
}

const cssId = id => /^[A-Za-z_][\w-]*$/.test(id) ? `#${id}` : `[id=${JSON.stringify(id)}]`;

export function locatorStrategies(element) {
  const strategies = [];
  if (stableId(element.id)) {
    const selector = cssId(element.id);
    strategies.push({kind:'id',expression:`page.locator(${JSON.stringify(selector)})`,
      build:page => page.locator(selector)});
  }
  if (element.testId) strategies.push({kind:'testId',
    expression:`page.getByTestId(${JSON.stringify(element.testId)})`,
    build:page => page.getByTestId(element.testId)});
  if (element.role && element.name) strategies.push({kind:'roleName',
    expression:`page.getByRole(${JSON.stringify(element.role)}, { name: ${JSON.stringify(element.name)}, exact: true })`,
    build:page => page.getByRole(element.role,{name:element.name,exact:true})});
  if (element.label) strategies.push({kind:'label',
    expression:`page.getByLabel(${JSON.stringify(element.label)}, { exact: true })`,
    build:page => page.getByLabel(element.label,{exact:true})});
  if (element.text) strategies.push({kind:'text',
    expression:`page.getByText(${JSON.stringify(element.text)}, { exact: true })`,
    build:page => page.getByText(element.text,{exact:true})});
  if (element.placeholder) strategies.push({kind:'placeholder',
    expression:`page.getByPlaceholder(${JSON.stringify(element.placeholder)}, { exact: true })`,
    build:page => page.getByPlaceholder(element.placeholder,{exact:true})});
  if (element.path) strategies.push({kind:'css',
    expression:`page.locator(${JSON.stringify(element.path)})`,
    build:page => page.locator(element.path)});
  return strategies;
}

export async function resolveLocator(page, target, inventory, options = {}) {
  const picked = chooseCandidate(target,inventory,options);
  if (picked.status !== 'resolved') return picked;
  for (const strategy of locatorStrategies(picked.candidate)) {
    try {
      const locator = strategy.build(page);
      if (await locator.count() !== 1) continue;
      if (strategy.kind === 'css') {
        const stillSame = await locator.evaluate((element, expected) =>
          element.tagName.toLowerCase() === expected.tag &&
          (!expected.text || String(element.innerText || '').replace(/\s+/g,' ').trim().slice(0,300) === expected.text),
        picked.candidate);
        if (!stillSame) continue;
      }
      return {...picked,status:'resolved',strategy:strategy.kind,
        expression:strategy.expression,locator};
    } catch { /* A strategy can be unavailable during a UI transition; try the next one. */ }
  }
  return {status:'unresolved',candidates:[picked.candidate]};
}
