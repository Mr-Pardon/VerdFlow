import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { createRenderer } from 'vue';

const server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
  optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false } });
after(() => server.close());
const { useResourceImport } = await server.ssrLoadModule('/src/features/onboarding/useResourceImport.ts');
const { useAssetLibrary } = await server.ssrLoadModule('/src/features/library/useAssetLibrary.ts');
const { activeConfirmation, settleConfirmation } = await server.ssrLoadModule('/src/components/confirmDialog.ts');
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

test('local VFA imports confirm only real conflicts and can skip one file without failing the batch', async () => {
  const calls = [];
  const identity = { expected_generation: 2, expected_catalog_hash: 'old-hash', expected_package_hash: 'vfa-hash' };
  const catalog = { target_type: 'dts', catalog_id: 'trees' };
  const response = (status, body) => ({ ok: true, value: { status, body: JSON.stringify(body) } });
  globalThis.window = { verdflowPlatform: {
    selectSourceFiles: async () => ({ ok: true, value: ['skip.vfa', 'replace.vfa', 'same.vfa'] }),
    requestLocalApi: async ({ path, body }) => {
      if (!body) return response(200, { items: [] });
      assert.equal(path, '/api/v1/asset-catalog-packages/import');
      const request = JSON.parse(body);
      calls.push(request);
      if (request.source_path === 'same.vfa') return response(201, { catalog, already_installed: true, replaced: false });
      if (request.replace_existing) return response(201, { catalog, already_installed: false, replaced: true });
      return response(409, { error: { code: 'asset_catalog_overwrite_required', details: {
        catalog_id: 'trees', existing_name: 'Old trees', incoming_name: request.source_path, ...identity,
      } } });
    },
  } };
  try {
    const library = useAssetLibrary();
    const importing = library.importCatalogPackage();
    await new Promise(resolve => setImmediate(resolve));
    assert.match(activeConfirmation.value.message, /skip\.vfa/);
    assert.equal(calls.length, 1);
    settleConfirmation(false);
    await new Promise(resolve => setImmediate(resolve));
    assert.match(activeConfirmation.value.message, /replace\.vfa/);
    settleConfirmation(true);
    assert.equal(await importing, true);
    assert.equal(activeConfirmation.value, null);
    assert.deepEqual(calls, [
      { source_path: 'skip.vfa', replace_existing: false },
      { source_path: 'replace.vfa', replace_existing: false },
      { source_path: 'replace.vfa', replace_existing: true, ...identity },
      { source_path: 'same.vfa', replace_existing: false },
    ]);
    assert.equal(library.errorMessage.value, null);
    assert.equal(library.noticeMessage.value, '已覆盖 1 个，已存在 1 个，已跳过 1 个');
  } finally {
    delete globalThis.window;
  }
});
