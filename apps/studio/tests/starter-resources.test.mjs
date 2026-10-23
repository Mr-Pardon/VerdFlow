import assert from "node:assert/strict";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createRenderer } from "vue";

const server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), configFile: false,
  optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false } });
after(() => server.close());
const { useStarterResources } = await server.ssrLoadModule("/src/features/onboarding/useStarterResources.ts");
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode: () => null, nextSibling: () => null });
const manifest = { revision: "a".repeat(64), resources: [{ id: "sample", name: "Sample", bytes: 4 }] };
const operation = { operation_id: "operation", status: "running", items: [] };
function mount(overrides = {}) {
  const calls = [];
  let state;
  const client = { starterResources: async () => { calls.push("manifest"); return manifest; },
    importStarterResources: async revision => { calls.push(revision); return operation; },
    starterImport: async () => ({ ...operation, status: "completed" }),
    cancelStarterImport: async id => { calls.push(`cancel:${id}`); return { ...operation, status: "cancelled" }; }, ...overrides };
  const app = renderer.createApp({ setup() { state = useStarterResources(client); return () => null; } });
  app.mount({});
  return { app, state, calls };
}
const settle = () => new Promise(resolve => setTimeout(resolve, 20));

test("opening is opt-in; import uses the displayed revision and completion ends polling", async () => {
  const value = mount();
  try {
    assert.deepEqual(value.calls, []);
    await value.state.load();
    assert.deepEqual(value.calls, ["manifest"]);
    await value.state.start();
    await settle();
    assert.equal(value.state.operation.value.status, "completed");
    assert.deepEqual(value.calls, ["manifest", manifest.revision]);
    assert.equal(value.state.running.value, false);
  } finally { value.app.unmount(); }
});

test("unmount cancels a late accepted submission without resuming polling", async () => {
  let resolve;
  const value = mount({ importStarterResources: () => new Promise(done => { resolve = done; }) });
  await value.state.load();
  const submission = value.state.start();
  value.app.unmount();
  resolve(operation);
  await submission;
  assert.deepEqual(value.calls, ["manifest", "cancel:operation"]);
  assert.equal(value.state.operation.value, undefined);
});

test("failed status reads preserve the active operation so it can be refreshed or cancelled", async () => {
  const value = mount({ starterImport: async () => { throw Error("offline"); } });
  try {
    await value.state.load(); await value.state.start(); await settle();
    assert.equal(value.state.running.value, true);
    assert.equal(value.state.error.value, "offline");
    await value.state.cancel();
    assert.equal(value.state.running.value, false);
    assert.ok(value.calls.includes("cancel:operation"));
  } finally { value.app.unmount(); }
});

test("failed manifest reads never submit downloads and late reads cannot mutate an unmounted view", async () => {
  const value = mount({ starterResources: async () => { throw Error("unavailable"); } });
  await value.state.load(); await value.state.start();
  assert.equal(value.state.error.value, "unavailable");
  assert.deepEqual(value.calls, []);
  value.app.unmount();
  let resolve;
  const late = mount({ starterResources: () => new Promise(done => { resolve = done; }) });
  const loading = late.state.load(); late.app.unmount(); resolve(manifest); await loading;
  assert.equal(late.state.manifest.value, undefined);
});
