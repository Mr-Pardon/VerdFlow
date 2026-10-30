import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createRenderer } from 'vue';

const server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
  optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false } });
after(() => server.close());
const { useResourceImport } = await server.ssrLoadModule('/src/features/onboarding/useResourceImport.ts');
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode: () => null, nextSibling: () => null });
const operation = { operation_id: 'op', status: 'awaiting_confirmation', items: [], choices: [
  { id: 'asset', optional: true }, { id: 'plan', conflict: true }], warnings: [] };
function mount(overrides = {}) {
  const calls = []; let state;
  const client = {
    prepareResourceImport: async (...args) => { calls.push(['prepare', ...args]); return operation; },
    confirmResourceImport: async (...args) => { calls.push(['confirm', ...args]); return { ...operation, status: 'completed' }; },
    starterImport: async () => operation,
    cancelStarterImport: async id => { calls.push(['cancel', id]); return { ...operation, status: 'cancelled' }; }, ...overrides };
  const app = renderer.createApp({ setup() { state = useResourceImport(['plan.vfp'], client); return () => null; } });
  app.mount({}); return { app, state, calls };
}
test('optional assets and replacement remain unchecked until explicitly chosen', async () => {
  const v = mount();
  await v.state.prepare();
  assert.deepEqual(v.state.optionalIds.value, []);
  assert.deepEqual(v.state.overwriteIds.value, []);
  v.state.optionalIds.value = ['asset'];
  await v.state.confirm();
  assert.deepEqual(v.calls[1], ['confirm', 'op', ['asset'], []]);
  v.app.unmount();
});
test('unmount releases pending confirmation and late accepted operations', async () => {
  const v = mount(); await v.state.prepare(); v.app.unmount();
  assert.deepEqual(v.calls.at(-1), ['cancel', 'op']);
  let resolve;
  const late = mount({ prepareResourceImport: () => new Promise(done => { resolve = done; }) });
  const request = late.state.prepare(); late.app.unmount(); resolve(operation); await request;
  assert.deepEqual(late.calls, [['cancel', 'op']]);
  assert.equal(late.state.operation.value, undefined);
});
test('choosing a local strategy replaces the old confirmation and clears selections', async () => {
  const v = mount(); await v.state.prepare(); v.state.optionalIds.value = ['asset'];
  await v.state.prepare(['strategy.vfs']);
  assert.deepEqual(v.calls.slice(1), [['cancel', 'op'], ['prepare', ['plan.vfp'], false, ['strategy.vfs']]]);
  assert.deepEqual(v.state.optionalIds.value, []); v.app.unmount();
});
