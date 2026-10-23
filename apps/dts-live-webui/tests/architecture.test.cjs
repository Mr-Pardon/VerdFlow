const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { parse } = require('vue/compiler-sfc');
const root = path.resolve(__dirname, '../src');
const files = fs.readdirSync(root, { recursive: true }).filter(file => /\.(ts|vue)$/.test(file) && !file.endsWith('.d.ts'));
const dependencies = new Map();
for (const file of files) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const script = file.endsWith('.vue') ? parse(source).descriptor.scriptSetup.content : source;
  const ast = ts.createSourceFile(file, script, ts.ScriptTarget.Latest, true);
  const imports = ast.statements.filter(ts.isImportDeclaration).map(node => node.moduleSpecifier.text);
  const resolved = imports.filter(value => value.startsWith('.')).flatMap(value => {
    const target = path.relative(root, path.resolve(root, path.dirname(file), value));
    const match = files.find(candidate => candidate === target || candidate === target + '.ts' || candidate === target + '.vue');
    return match ? [match] : [];
  });
  dependencies.set(file, resolved);
  if (file === 'App.vue' || file.startsWith('components' + path.sep)) {
    assert.ok(!imports.some(value => /(?:^|\/)(api|sdk)(?:\/|$)/.test(value)), file + ' must not own transport or SDK calls');
  }
  if (file.startsWith('sdk' + path.sep)) {
    assert.ok(!imports.some(value => /(?:^|\/)(features|components)(?:\/|$)/.test(value)), file + ' must not depend on application state');
  }
}
test('WebUI dependency graph has no cycles or orphan source modules', () => {
  const visiting = new Set(), visited = new Set();
  const visit = file => {
    assert.ok(!visiting.has(file), 'Module cycle: ' + file);
    if (visited.has(file)) return;
    visiting.add(file);
    for (const target of dependencies.get(file) ?? []) visit(target);
    visiting.delete(file); visited.add(file);
  };
  visit('main.ts');
  assert.deepEqual(files.filter(file => !visited.has(file)), []);
});
test('Root view owns composition, not polling, storage or SDK execution', () => {
  const source = fs.readFileSync(path.join(root, 'App.vue'), 'utf8');
  assert.ok(!/localStorage|setInterval|fetch\(|new DtsSdkBridge/.test(source));
});

test('Shared preview presentation has no application-private or transport dependency', () => {
  const shared = path.resolve(root, '../../../packages/strategy-preview-ui/src');
  for (const file of fs.readdirSync(shared).filter(file => /\.(ts|vue)$/.test(file))) {
    const source = fs.readFileSync(path.join(shared, file), 'utf8');
    assert.ok(!/from\s+["'][^"']*(?:apps\/|platform|api["']|sdk\/)/.test(source), file + ' depends on an application');
    assert.ok(!/fetch\(|localStorage|new LocalApiClient|XMLHttpRequest/.test(source), file + ' owns transport or persistence');
  }
});

test('Shared asset browser owns no application transport or execution capabilities', () => {
  const shared = path.resolve(root, '../../../packages/asset-browser-ui/src');
  for (const file of fs.readdirSync(shared).filter(file => /\.(ts|vue)$/.test(file))) {
    const source = fs.readFileSync(path.join(shared, file), 'utf8');
    assert.ok(!/from\s+["'][^"']*(?:apps\/|platform|api["']|sdk\/)/.test(source));
    assert.ok(!/fetch\(|new LocalApiClient|XMLHttpRequest|executeStrategy/.test(source));
  }
});
