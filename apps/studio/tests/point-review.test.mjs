import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { createServer } from "vite";
import { createRenderer } from "vue";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  configFile: false,
  cacheDir: fileURLToPath(new URL("../../../tests/.tmp/point-review-vite", import.meta.url)),
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true, hmr: false, ws: false },
});
let respond;
globalThis.window = { verdflowPlatform: {
  requestLocalApi: async (request) => {
    try {
      return { ok: true, value: { status: 200, body: JSON.stringify(await respond(request)) } };
    } catch (error) {
      return { ok: false, message: error.message };
    }
  },
} };
const { usePointReview } = await server.ssrLoadModule("/src/features/dts/usePointReview.ts");
const renderer = createRenderer({
  createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
  parentNode: () => null, nextSibling: () => null,
});
const bounds = (minX = 0, maxX = 10) => ({ min_x: minX, max_x: maxX, min_y: 0, max_y: 10, min_z: 0, max_z: 0 });
const sources = [0, 1, 2].map((index) => ({ key: `batch-${index}`, pointSetId: `points-${index}`, assignmentSetId: `assignments-${index}` }));
const manifest = (index) => ({
  point_count: 120_000, region_set_id: "regions", max_resolved_radius_m: 0,
  bounds: { ...bounds(index * 1000, index * 1000 + 100), max_y: 100 }, assets: [],
});
const empty = { items: [], truncated: false, next_after_ordinal: null };
const requests = [];
const api = async (request) => {
  const url = new URL(request.path, "http://localhost");
  if (url.pathname.endsWith("/review")) return manifest(Number(url.pathname.match(/points-(\d)/)[1]));
  if (url.pathname.endsWith("/viewer")) return { region_set_id: "regions", region_count: 2, bounds: bounds(0, 2100) };
  requests.push(url);
  if (url.pathname.endsWith("/geometry")) return { ...empty, items: [{ region_id: "unplanted" }] };
  if (url.pathname.endsWith("/points")) return { ...empty, items: [{ point_id: url.searchParams.get("min_x") }] };
  throw new Error(`Unexpected request ${request.path}`);
};
const mount = (selectedSources = sources) => {
  let state;
  const app = renderer.createApp({ setup() {
    state = usePointReview({ sessionId: "session", sources: selectedSources });
    return () => null;
  } });
  app.mount({});
  return { state, unmount: () => app.unmount() };
};
const flush = () => new Promise((resolve) => setImmediate(resolve));

try {
  await test("point review bounds work and coalesces viewport requests", async (t) => {
    await t.test("overview skips points and zoom skips offscreen batches", async () => {
      requests.length = 0; respond = api;
      const panel = mount();
      try {
        await panel.state.initialize();
        assert.equal(panel.state.errorMessage.value, null);
        assert.equal(panel.state.regionManifest.value.region_set_id, "regions");
        await panel.state.loadViewport({ ...bounds(0, 2100), max_y: 100 });
        assert.equal(panel.state.pointDetailsVisible.value, false);
        assert.equal(requests.filter((url) => url.pathname.endsWith("/points")).length, 0);
        await panel.state.loadViewport(bounds());
        const points = requests.filter((url) => url.pathname.endsWith("/points"));
        assert.equal(points.length, 1);
        assert.match(points[0].pathname, /points-0/);
        assert.equal(panel.state.regions.value[0].region_id, "unplanted");
      } finally { panel.unmount(); }
    });

    await t.test("rapid camera changes keep only the newest pending viewport", async () => {
      requests.length = 0;
      let release;
      const firstPage = new Promise((resolve) => { release = resolve; });
      let pointRequests = 0;
      respond = async (request) => {
        if (new URL(request.path, "http://localhost").pathname.endsWith("/points") && ++pointRequests === 1) {
          requests.push(new URL(request.path, "http://localhost"));
          return firstPage;
        }
        return api(request);
      };
      const panel = mount();
      try {
        await panel.state.initialize();
        const active = panel.state.loadViewport(bounds());
        await flush();
        panel.state.loadViewport(bounds(2, 12));
        panel.state.loadViewport(bounds(4, 14));
        const last = panel.state.loadViewport(bounds(6, 16));
        assert.equal(active, last);
        assert.equal(pointRequests, 1);
        release({ items: [{ point_id: "obsolete" }], truncated: true, next_after_ordinal: 1 });
        await active;
        assert.equal(pointRequests, 2);
        assert.equal(requests.filter((url) => url.pathname.endsWith("/geometry")).length, 2);
        assert.equal(panel.state.pointsBySource.value["batch-0"][0].point_id, "6");
        assert.equal(panel.state.errorMessage.value, null);
        assert.equal(panel.state.loadingPoints.value, false);
      } finally { release(empty); panel.unmount(); }
    });

    await t.test("leaving the panel discards pending views and late responses", async () => {
      let release;
      let pointRequests = 0;
      respond = (request) => {
        if (request.path.includes("/points?")) {
          pointRequests++;
          return new Promise((resolve) => { release = resolve; });
        }
        return api(request);
      };
      const panel = mount();
      await panel.state.initialize();
      const active = panel.state.loadViewport(bounds());
      await flush();
      panel.state.loadViewport(bounds(5, 15));
      panel.unmount();
      release({ ...empty, items: [{ point_id: "late" }] });
      await active;
      assert.equal(pointRequests, 1);
      assert.deepEqual(panel.state.pointsBySource.value, {});
    });

    await t.test("connection failure is readable and retry uses the current view", async () => {
      let fail = true;
      respond = (request) => {
        if (fail && request.path.includes("/points?")) throw new Error("fetch failed");
        return api(request);
      };
      const panel = mount();
      try {
        await panel.state.initialize();
        await panel.state.loadViewport(bounds(3, 13));
        assert.match(panel.state.errorMessage.value, /本地服务连接暂时失败/);
        fail = false;
        await panel.state.retry();
        assert.equal(panel.state.errorMessage.value, null);
        assert.equal(panel.state.pointsBySource.value["batch-0"][0].point_id, "3");
      } finally { panel.unmount(); }
    });

    await t.test("reference radius padding and zero-area point bounds remain visible", async () => {
      requests.length = 0;
      respond = (request) => request.path.includes("/review?")
        ? { ...manifest(0), point_count: 1, bounds: { ...bounds(11, 11), min_y: 5, max_y: 5 }, max_resolved_radius_m: 3 }
        : api(request);
      const panel = mount([sources[0]]);
      try {
        await panel.state.initialize();
        await panel.state.loadViewport(bounds());
        assert.equal(requests.filter((url) => url.pathname.endsWith("/points")).length, 1);
        assert.equal(panel.state.pointsBySource.value["batch-0"].length, 1);
      } finally { panel.unmount(); }
    });

    await t.test("retry preserves initialization failures and unmount discards late manifests", async () => {
      respond = () => { throw new Error("fetch failed"); };
      const failed = mount();
      try {
        await failed.state.initialize();
        await failed.state.retry();
        assert.match(failed.state.errorMessage.value, /本地服务连接暂时失败/);
        assert.equal(failed.state.regionManifest.value, null);
      } finally { failed.unmount(); }
      let release;
      respond = () => new Promise((resolve) => { release = resolve; });
      const late = mount([sources[0]]);
      const initialization = late.state.initialize();
      late.unmount();
      release(manifest(0));
      await initialization;
      assert.deepEqual(late.state.manifests.value, {});
      assert.equal(late.state.regionManifest.value, null);
    });
  });
} finally {
  await server.close();
  delete globalThis.window;
}
