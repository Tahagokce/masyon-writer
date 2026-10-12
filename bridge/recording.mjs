import ts from 'typescript';

export function rewritePage(code, alias) {
  const source = ts.createSourceFile('action.ts', code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  if (source.parseDiagnostics.length) throw new Error('Invalid Playwright-generated TypeScript');
  // Rename identifiers only. Never replace the word "page" in strings/selectors.
  const result = ts.transform(source, [context => {
    const visit = node => {
      if (ts.isParameter(node) && ts.isIdentifier(node.name) && node.name.text === 'page')
        return ts.factory.updateParameterDeclaration(node, node.modifiers, node.dotDotDotToken,
          ts.factory.createIdentifier(alias), node.questionToken,
          ts.factory.createTypeReferenceNode('Page'), node.initializer);
      if (ts.isIdentifier(node) && node.text === 'page') return ts.factory.createIdentifier(alias);
      if (ts.isElementAccessExpression(node) && node.expression.getText(source) === 'process.env')
        return ts.factory.createNonNullExpression(node);
      return ts.visitEachChild(node, visit, context);
    };
    return node => ts.visitNode(node, visit);
  }]);
  const output = ts.createPrinter().printFile(result.transformed[0]);
  result.dispose();
  return output.trim();
}

export class Journal {
  constructor() { this.entries = []; this.active = false; this.problem = ''; }
  start() { this.entries = []; this.active = true; this.problem = ''; }
  begin(alias, tool, mutating) {
    const entry = {alias, tool, mutating, code: '', before: [], pages: []};
    this.entries.push(entry);
    return entry;
  }
  render(status, reason) {
    this.active = false;
    const lines = ["import { test, expect, type Page } from '@playwright/test';", '',
      '// Actual Playwright MCP action code; see the accompanying run report for provenance.',
      "test('recorded Jira case', async ({ page, context }) => {"];
    this.entries.forEach((entry, index) => {
      if (!entry.code && !entry.pages.length && !entry.before.length) return;
      lines.push(`  // ${entry.tool}`);
      if (entry.pages.length) {
        lines.push(`  const opened${index}: Page[] = [];`,
          `  const capture${index} = (p: Page) => opened${index}.push(p);`,
          `  context.on('page', capture${index});`);
      }
      for (const before of entry.before) lines.push('  ' + before);
      if (entry.code) lines.push(rewritePage(entry.code, entry.alias).split('\n').map(s => '  ' + s).join('\n'));
      if (entry.pages.length) {
        lines.push(`  await expect.poll(() => opened${index}.length).toBe(${entry.pages.length});`,
          `  context.off('page', capture${index});`);
        entry.pages.forEach((p, i) => {
          lines.push(`  const ${p.alias} = opened${index}[${i}];`);
          if (p.opener) lines.push(`  expect(await ${p.alias}.opener()).toBe(${p.opener});`);
        });
      }
    });
    if (this.problem) lines.push(`  throw new Error(${JSON.stringify(`BLOCKED: partial recording; ${this.problem}`)});`);
    else if (status !== 'SUCCESS') lines.push(`  throw new Error(${JSON.stringify(`${status}: ${reason}`)});`);
    lines.push('});', '');
    const code = lines.join('\n');
    const source = ts.createSourceFile('recording.spec.ts', code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    if (source.parseDiagnostics.length) throw new Error('Recording TypeScript syntax validation failed');
    return code;
  }
}
