import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createRenderer } from 'vue';

const server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
  optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false } });
after(() => server.close());
const { useResourceCenter } = await server.ssrLoadModule('/src/features/resources/useResourceCenter.ts');
const { useStrategyUpdates } = await server.ssrLoadModule('/src/features/resources/useStrategyUpdates.ts');
const { useResourceImport } = await server.ssrLoadModule('/src/features/onboarding/useResourceImport.ts');
const { renderResourceMarkdown, safeContentImage } = await server.ssrLoadModule('/src/features/resources/resourcePresentation.ts');
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode: () => null, nextSibling: () => null });
function mount(factory) { let state; const app = renderer.createApp({ setup() { state = factory(); return () => null; } }); app.mount({}); return { app, state }; }

test('markdown never executes remote HTML or renders arbitrary remote media', () => {
  const base = 'https://mr-pardon.github.io/verdflow-resources/data/sample/aaaaaaaaaaaaaaaaaaaa/';
  const html = renderResourceMarkdown('<script>alert(1)</script>\n\n![bad](https://evil.test/a.png)\n\n![ok](media/cover.png)\n\n[manual](https://mr-pardon.github.io/verdflow-site/resources/strategies.html)\n\n[bad](javascript:alert(1))', base);
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('src="https://evil'));
  assert.ok(html.includes('src="' + base + 'media/cover.png"'));
  assert.ok(html.includes('rel="noopener noreferrer"'));
  assert.ok(!html.includes('href="javascript:'));
  for (const url of ['data:image/svg+xml,<svg/>', 'https://evil.test/a.png', base + 'x.svg', 'file:///x.png']) assert.equal(safeContentImage(url), '');
});

test('switching details and leaving the route discard late responses', async () => {
  const pending = [];
  const v = mount(() => useResourceCenter({ resourceCenterDetail: id => new Promise(resolve => pending.push({ id, resolve })) }));
  const first = v.state.open('one'); const second = v.state.open('two');
  pending[1].resolve({ resource: { id: 'two' }, warning: '' }); await second;
  pending[0].resolve({ resource: { id: 'one' }, warning: '' }); await first;
  assert.equal(v.state.detail.value.resource.id, 'two');
  const third = v.state.open('three'); v.app.unmount(); pending[2].resolve({ resource: { id: 'three' } }); await third;
  assert.equal(v.state.detail.value, undefined);
});

test('refresh failures retain the loaded catalog and can be dismissed', async () => {
  let fail = false;
  const v = mount(() => useResourceCenter({ resourceCenter: async () => { if (fail) throw Error('offline'); return { resources: [{ id: 'sample' }], warning: '' }; } }));
  await v.state.load(); fail = true; await v.state.load(true);
  assert.equal(v.state.catalog.value.resources.length, 1); assert.equal(v.state.error.value, 'offline');
  v.state.error.value = ''; assert.equal(v.state.error.value, ''); v.app.unmount();
});

test('a center import sends only the reviewed selection and cancels on close', async () => {
  const calls = [];
  const selection = { resource_id: 'sample', version: '1.2.0', revision: 'a'.repeat(64) };
  const v = mount(() => useResourceImport([], {
    prepareResourceImport: async (...args) => { calls.push(args); return { operation_id: 'op', status: 'awaiting_confirmation', items: [] }; },
    cancelStarterImport: async id => { calls.push(['cancel', id]); },
  }, selection));
  await v.state.prepare(); assert.deepEqual(calls[0], [[], false, [], selection]);
  v.app.unmount(); assert.deepEqual(calls[1], ['cancel', 'op']);
});

test('update checks are discovery-only and ignore results after disposal', async () => {
  let resolve;
  const v = mount(() => useStrategyUpdates({ strategyResourceUpdates: () => new Promise(done => resolve = done) }));
  const request = v.state.check(); v.app.unmount(); resolve({ items: [{ resource_id: 'sample' }], warning: '' }); await request;
  assert.deepEqual(v.state.items.value, []);
});
