import assert from "node:assert/strict";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createRenderer, nextTick, ref } from "vue";

const server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), configFile: false,
  optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false } });
after(() => server.close());
const { useFirstUseGuide } = await server.ssrLoadModule("/src/features/onboarding/useFirstUseGuide.ts");
const { readProgress, initialProgress, guideStorageKey, guideSteps } = await server.ssrLoadModule("/src/features/onboarding/steps.ts");
const { runtimeReadiness } = await server.ssrLoadModule("/src/features/onboarding/runtimeReadiness.ts");
const { hasExistingSetup } = await server.ssrLoadModule("/src/features/onboarding/existingSetup.ts");
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode: () => null, nextSibling: () => null });
const flush = async () => { await nextTick(); await Promise.resolve(); await nextTick(); };
function mount(overrides = {}) {
  const path = ref("/projects"), connected = ref(false), data = new Map();
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
  const calls = [];
  let guide;
  const app = renderer.createApp({ setup() { guide = useFirstUseGuide({ storage, path: () => path.value,
    connected: () => connected.value, navigate: async value => { calls.push(value); path.value = value; },
    hasExistingSetup: async () => false, ...overrides }); return () => null; } });
  app.mount({});
  return { app, guide, path, connected, data, calls };
}

test("progress is versioned, bounded and stores viewed/skipped rather than readiness", () => {
  for (const raw of [null, "bad", "{}", '{"version":2}', JSON.stringify({ ...initialProgress(), step: "missing" })]) assert.equal(readProgress(raw), null);
  const read = readProgress(JSON.stringify({ ...initialProgress(), visited: ["assets", "assets", "secret"], skipped: ["models"], apiKey: "never-retain", ready: true }));
  assert.deepEqual(read.visited, ["assets"]);
  assert.equal("ready" in read, false);
  assert.equal("apiKey" in read, false);
});

test("new empty installation offers welcome only, with no import, trust or navigation", async () => {
  let probes = 0;
  const state = mount({ hasExistingSetup: async () => { probes++; return false; } });
  try {
    state.connected.value = true; await flush();
    assert.equal(state.guide.surface.value, "welcome");
    assert.deepEqual(state.calls, []);
    state.connected.value = false; await flush(); state.connected.value = true; await flush();
    assert.equal(probes, 1);
    state.guide.dismiss();
    assert.equal(JSON.parse(state.data.get(guideStorageKey)).status, "dismissed");
  } finally { state.app.unmount(); }
});

test("existing configuration and failed reads never trigger an upgrade welcome", async () => {
  for (const hasExistingSetup of [async () => true, async () => { throw Error("offline"); }]) {
    const state = mount({ hasExistingSetup });
    try { state.connected.value = true; await flush(); assert.equal(state.guide.surface.value, "hidden"); }
    finally { state.app.unmount(); }
  }
});

test("a fast service response before the initial router redirect still offers the guide", async () => {
  const state = mount();
  try {
    state.path.value = "/"; state.connected.value = true; await flush();
    assert.equal(state.guide.surface.value, "hidden");
    state.path.value = "/projects"; await flush();
    assert.equal(state.guide.surface.value, "welcome");
  } finally { state.app.unmount(); }
});

test("late boot queries cannot reopen a dismissed guide or update an unmounted shell", async () => {
  for (const unmount of [false, true]) {
    let resolve;
    const state = mount({ hasExistingSetup: () => new Promise(done => { resolve = done; }) });
    state.connected.value = true; await flush();
    if (unmount) state.app.unmount(); else { state.guide.open(); state.guide.dismiss(); }
    resolve(false); await flush();
    assert.equal(state.guide.surface.value, "hidden");
    if (!unmount) state.app.unmount();
  }
});

test("skip survives restart, manual navigation collapses, page registrations clean up", async () => {
  const state = mount();
  try {
    await state.guide.start();
    const old = state.guide.registerPage("assets", () => ({ ready: false, status: "empty" }));
    const remove = state.guide.registerPage("assets", () => ({ ready: true, status: "loaded" }));
    old(); assert.equal(state.guide.page.value.status, "loaded");
    remove(); assert.equal(state.guide.page.value, undefined);
    await state.guide.next(true);
    assert.equal(state.path.value, "/strategies");
    assert.deepEqual(state.guide.progress.value.skipped, ["assets"]);
    state.path.value = "/dts/realtime"; await flush();
    assert.equal(state.guide.surface.value, "collapsed");
    assert.equal(state.calls.length, 2);
    const saved = state.data.get(guideStorageKey);
    const restored = mount({ storage: { getItem: () => saved, setItem() {} } });
    try { assert.equal(restored.guide.surface.value, "collapsed"); assert.equal(restored.guide.step.value.id, "strategies"); assert.deepEqual(restored.calls, []); }
    finally { restored.app.unmount(); }
  } finally { state.app.unmount(); }
});

test("business actions require an explicit click and prevent advancing while busy", async () => {
  const state = mount();
  let executed = 0, resolve;
  try {
    const action = { label: "Import", run: () => { executed++; return new Promise(done => { resolve = done; }); } };
    state.guide.registerPage("assets", () => ({ ready: false, status: "empty", actions: [action] }));
    await state.guide.start();
    assert.equal(executed, 0);
    await state.guide.run({ ...action, disabled: true }); assert.equal(executed, 0);
    const running = state.guide.run(action);
    await state.guide.next(); assert.equal(state.guide.step.value.id, "assets");
    await state.guide.run(action); assert.equal(executed, 1);
    resolve(); await running;
    assert.equal(state.guide.page.value.ready, false);
    await state.guide.next(true); assert.equal(state.guide.step.value.id, "strategies");
  } finally { state.app.unmount(); }
});

test("runtime readiness follows the selected workflow, not both by default", () => {
  const runtimes = [{ runtime_id: "dts-explorer", available: true }, { runtime_id: "blender", available: false }];
  assert.equal(runtimeReadiness(runtimes, "live").ready, true);
  assert.equal(runtimeReadiness(runtimes, "fbx").ready, false);
  assert.match(runtimeReadiness(runtimes, "both").status, /Blender/);
  assert.equal(runtimeReadiness([], "live").ready, false);
});

test("tour completion records skipped steps without asserting environment readiness", async () => {
  const state = mount();
  try {
    await state.guide.start();
    for (const step of guideSteps) {
      assert.equal(state.guide.step.value.id, step.id);
      await state.guide.next(true);
    }
    assert.equal(state.guide.surface.value, "finish");
    assert.equal(state.guide.progress.value.status, "completed");
    assert.equal(state.guide.progress.value.skipped.length, 7);
    assert.ok(!state.calls.some(path => path.includes("realtime")));
    state.guide.open(); await state.guide.start();
    assert.equal(state.guide.step.value.id, "assets");
    assert.deepEqual(state.guide.progress.value.skipped, []);
  } finally { state.app.unmount(); }
});

test("existing setup detection includes both catalogs, user configuration and projects", async () => {
  const empty = async () => ({ items: [] });
  const client = { assetCatalogs: empty, strategies: empty, modelProviders: empty, externalRuntimes: empty };
  assert.equal(await hasExistingSetup(client, () => false), false);
  assert.equal(await hasExistingSetup(client, () => true), true);
  assert.equal(await hasExistingSetup({ ...client, assetCatalogs: async target => ({ items: target === "blender" ? [{}] : [] }) }, () => false), true);
  assert.equal(await hasExistingSetup({ ...client, externalRuntimes: async () => ({ items: [{ available: true, configured_path: null }] }) }, () => false), false);
  assert.equal(await hasExistingSetup({ ...client, externalRuntimes: async () => ({ items: [{ configured_path: "local-only" }] }) }, () => false), true);
});

test("author introductions distinguish optional agent tools from importable packages", () => {
  assert.match(guideSteps[0].author.description, /Agent.*\.vfa/);
  assert.match(guideSteps[1].author.description, /Agent.*\.vfs/);
  for (const step of guideSteps.slice(0, 2)) assert.match(step.author.description, /ZIP.*不是/);
});

test("blocked navigation and unavailable storage keep the guide usable", async () => {
  const state = mount({ storage: { getItem() { throw Error("blocked"); }, setItem() { throw Error("blocked"); } }, navigate: async () => undefined });
  try {
    state.guide.open(); await state.guide.start();
    assert.equal(state.guide.surface.value, "collapsed");
    state.guide.dismiss(); assert.equal(state.guide.surface.value, "hidden");
  } finally { state.app.unmount(); }
});
