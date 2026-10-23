import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createRenderer, nextTick } from "vue";

const server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), configFile: false,
  optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false } });
const { useApplicationUpdates } = await server.ssrLoadModule("/src/features/settings/useApplicationUpdates.ts");
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode: () => null, nextSibling: () => null });

test("one update subscription survives settings navigation and is released on shell unmount", async () => {
  let subscriber, subscribed = 0, unsubscribed = 0, complete;
  const status = { supported: true, configured: true, stage: "idle", currentVersion: "0.3.1", autoCheck: true, autoDownload: true };
  const api = { status: async () => ({ ok: true, value: status }), subscribe: listener => { subscriber = listener; subscribed++; return () => unsubscribed++; },
    check: () => new Promise(resolve => { complete = resolve; }) };
  let updates;
  const app = renderer.createApp({ setup() { updates = useApplicationUpdates(api); return () => null; } });
  try {
    app.mount({}); await nextTick(); await Promise.resolve();
    assert.equal(subscribed, 1);
    subscriber({ ...status, stage: "ready", latestVersion: "0.4.0" });
    assert.equal(updates.attention.value, true);
    assert.match(updates.label.value, /0.4.0.*重启/);
    const action = updates.run("check");
    app.unmount();
    complete({ ok: true, value: { ...status, stage: "idle" } });
    await action;
    assert.equal(unsubscribed, 1);
    assert.equal(updates.state.value.stage, "ready");
  } finally { await server.close(); }
});
