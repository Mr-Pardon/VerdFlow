import assert from "node:assert/strict";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createRenderer, reactive, nextTick } from "vue";

const server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), configFile: false,
  optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false } });
after(() => server.close());
const { useAssetBrowser } = await server.ssrLoadModule("/@fs/" + fileURLToPath(new URL("../../../packages/asset-browser-ui/src/useAssetBrowser.ts", import.meta.url)));
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode: () => null, nextSibling: () => null });
const storage = new Map();
const listeners = new Set();
globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
globalThis.document = { addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) };
after(() => { delete globalThis.localStorage; delete globalThis.document; });
const catalogs = ['a', 'b'].map(catalog_id => ({ catalog_id, display_name: catalog_id, target_type: 'dts', provider_id: 'mesh' }));
const page = (request, revision = 'v1') => ({ items: [{ asset_id: request.query || 'plant', display_name: 'Tree' }], collections: [], total_count: 200,
  offset: request.offset, next_offset: request.offset + request.limit, revision, unavailable_catalogs: [] });
function mount(overrides = {}) {
  const calls = [];
  const props = reactive({ catalogs, targetType: 'dts', initialCatalogIds: [], selectedIds: ['saved-plant'],
    search: async (request, signal) => { calls.push({ request, signal }); return page(request); }, ...overrides });
  let state;
  const app = renderer.createApp({ setup() { state = useAssetBrowser(props); if (overrides.gridElement) state.grid.value = overrides.gridElement; return () => null; } });
  app.mount({});
  return { app, state, props, calls };
}
const settle = async () => { await nextTick(); await new Promise(resolve => setTimeout(resolve, 210)); };

test('multi-scope search retains text and selected plants; categories remain catalog-local', async () => {
  storage.clear();
  const v = mount();
  try {
    await settle(); assert.deepEqual(v.state.scope.value, ['a', 'b']);
    v.state.query.value = 'oak'; v.state.category.value = JSON.stringify(['a', 'trees']); await settle();
    assert.equal(v.calls.at(-1).request.collection_catalog_id, 'a');
    v.state.toggleScope('a'); await settle();
    assert.equal(v.state.query.value, 'oak');
    assert.equal(v.state.category.value, '');
    assert.deepEqual(v.calls.at(-1).request.catalog_ids, ['b']);
    assert.deepEqual(v.props.selectedIds, ['saved-plant']);
    v.state.setScope([]); await settle();
    assert.equal(v.state.result.value, null);
  } finally { v.app.unmount(); }
  const reopened = mount(); assert.deepEqual(reopened.state.scope.value, []); reopened.app.unmount();
});

test('saved sources initialize scope and incompatible providers never join a search', async () => {
  storage.clear();
  const v = mount({ initialCatalogIds: ['b'], catalogs: [...catalogs, { ...catalogs[0], catalog_id: 'foreign', provider_id: 'other' }], providerId: 'mesh' });
  try {
    await settle();
    assert.deepEqual(v.state.scope.value, ['b']);
    assert.deepEqual(v.state.compatible.value.map(c => c.catalog_id), ['a', 'b']);
  } finally { v.app.unmount(); }
});

test('debounced requests coalesce; stale responses and closed panels cannot overwrite results', async () => {
  storage.clear();
  const pending = [];
  const v = mount({ search: (request, signal) => new Promise(resolve => pending.push({ request, signal, resolve })) });
  v.state.query.value = 'o'; await nextTick(); v.state.query.value = 'oak'; await settle();
  assert.equal(pending.length, 2);
  assert.equal(pending[0].signal.aborted, true);
  pending[1].resolve(page(pending[1].request)); await nextTick(); await nextTick();
  pending[0].resolve(page(pending[0].request)); await nextTick(); await nextTick();
  assert.equal(v.state.result.value.items[0].asset_id, 'oak');
  v.state.query.value = 'last'; await settle();
  v.app.unmount(); assert.equal(pending[2].signal.aborted, true);
  pending[2].resolve(page(pending[2].request)); await nextTick(); await nextTick();
  assert.equal(v.state.result.value, null);
  assert.equal(listeners.size, 0);
});

test('page requests carry revision and honor server reset after catalog updates', async () => {
  storage.clear();
  const v = mount();
  try {
    await settle(); await v.state.load(48);
    assert.equal(v.calls.at(-1).request.revision, 'v1');
    v.props.search = async request => ({ ...page(request, 'v2'), offset: 0 });
    await v.state.load(96);
    assert.equal(v.state.offset.value, 0);
  } finally { v.app.unmount(); }
});

test('partial failures remain visible, and blocked local storage does not block search', async () => {
  storage.clear();
  const saved = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw Error('disabled'); }, setItem() { throw Error('disabled'); } };
  const v = mount({ search: async request => ({ ...page(request), unavailable_catalogs: [{ catalog_id: 'b', message: 'unavailable' }] }) });
  try {
    await settle(); assert.equal(v.state.result.value.items.length, 1); assert.equal(v.state.result.value.unavailable_catalogs.length, 1);
    v.state.toggleScope('b'); await settle(); assert.deepEqual(v.state.scope.value, ['a']);
  } finally { v.app.unmount(); globalThis.localStorage = saved; }
});

test('responsive pages fill whole rows, hidden grids retain size, and observers are released', async () => {
  storage.clear();
  let resize, disconnected = false, observed = false, columns = 5;
  globalThis.ResizeObserver = class { constructor(callback) { resize = callback; } observe() { observed = true; } disconnect() { disconnected = true; } };
  globalThis.getComputedStyle = () => ({ gridTemplateColumns: Array(columns).fill('140px').join(' ') });
  const gridElement = { clientWidth: 800, scrollTo() {} };
  const v = mount({ gridElement });
  try {
    assert.equal(observed, true);
    for (columns = 1; columns <= 9; columns++) {
      resize();
      assert.equal(v.state.pageSize.value % columns, 0);
      assert.ok(v.state.pageSize.value >= 48);
    }
    const size = v.state.pageSize.value;
    gridElement.clientWidth = 0; columns = 4; resize();
    assert.equal(v.state.pageSize.value, size);
  } finally {
    v.app.unmount(); assert.equal(disconnected, true);
    delete globalThis.ResizeObserver; delete globalThis.getComputedStyle;
  }
});
