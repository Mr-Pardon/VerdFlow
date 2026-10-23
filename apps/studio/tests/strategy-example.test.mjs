import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { createServer } from "vite";
import vue from "@vitejs/plugin-vue";
import { createRenderer, nextTick, ref } from "vue";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  configFile: false,
  plugins: [vue()],
  cacheDir: fileURLToPath(
    new URL("../../../tests/.tmp/strategy-example-vite", import.meta.url),
  ),
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true, hmr: false, ws: false },
});
let respond;
const calls = [];
globalThis.window = {
  verdflowPlatform: {
    requestLocalApi: async (request) => {
      calls.push(request);
      return {
        ok: true,
        value: { status: 200, body: JSON.stringify(await respond(request)) },
      };
    },
  },
};
const shared = await server.ssrLoadModule("@verdflow/strategy-preview-ui");
const { LocalApiClient } = await server.ssrLoadModule("/src/api/client.ts");
const { platformBridge } = await server.ssrLoadModule("/src/platform/index.ts");
const useStrategyExample = (input, cache) =>
  shared.useStrategyExample(input, cache, new LocalApiClient(platformBridge()));
const useExampleReferenceRanges = (geometry, appearance, seed) =>
  shared.useExampleReferenceRanges(
    geometry,
    appearance,
    seed,
    new LocalApiClient(platformBridge()),
  );
const { strategyExampleAppearance } = await server.ssrLoadModule(
  "/src/features/shared/strategyExampleAppearance.ts",
);
const renderer = createRenderer({
  createComment: () => ({}),
  createElement: () => ({}),
  createText: () => ({}),
  insert() {},
  remove() {},
  setText() {},
  setElementText() {},
  patchProp() {},
  parentNode: () => null,
  nextSibling: () => null,
});
const input = (value) => ({
  package_hash: "hash",
  strategy_parameters: { spacing: value },
  excluded_role_keys: [],
});
const operation = (id, status = "pending", count = 1) => ({
  preview_id: id,
  package_hash: "hash",
  status,
  error_message: null,
  result:
    status === "succeeded"
      ? {
          schema: "verdflow.strategy-example-preview",
          version: "1",
          package_hash: "hash",
          role_keys: ["tree"],
          points: Array.from({ length: count }, () => [1, 2, 0]),
        }
      : null,
});
function mount(initial = input(3), cache) {
  const source = ref(initial);
  let state;
  const app = renderer.createApp({
    setup() {
      state = useStrategyExample(() => source.value, cache);
      return () => null;
    },
  });
  app.mount({});
  return { state, source, unmount: () => app.unmount() };
}
const flush = async () => {
  await nextTick();
  await new Promise((resolve) => setImmediate(resolve));
};
const tick = async (t) => {
  t.mock.timers.tick(150);
  await flush();
};

try {
  await test("returning from mapping reuses session geometry, not another strategy run", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    calls.length = 0;
    respond = () => operation("cached", "succeeded");
    const cache = { signature: "", result: null };
    const first = mount(input(3), cache);
    await tick(t);
    first.unmount();
    const posts = calls.filter((call) => call.method === "POST").length;
    const second = mount(input(3), cache);
    await tick(t);
    assert.equal(calls.filter((call) => call.method === "POST").length, posts);
    assert.equal(second.state.result.value, cache.result);
    second.source.value = input(4);
    await flush();
    await tick(t);
    assert.equal(
      calls.filter((call) => call.method === "POST").length,
      posts + 1,
    );
    second.unmount();
  });
  await test("appearance honors saved snapshots, missing dimensions and draft scales", () => {
    const draft = (id, scaleMode = "catalog") => ({
      assetId: id,
      weight: 1,
      scaleMode,
      fixedScale: 1.2,
      minScale: 0.8,
      maxScale: 1.4,
    });
    const value = strategyExampleAppearance(
      {
        tree: [
          draft("saved"),
          draft("missing"),
          draft("new", "uniform_random"),
          draft("fixed", "fixed"),
        ],
      },
      {
        tree: {
          candidates: [
            {
              asset_id: "saved",
              reference_radius_m: 2,
              scale: { mode: "fixed", value: 0.9 },
            },
            { asset_id: "missing", scale: { mode: "fixed", value: 1 } },
          ],
        },
      },
      [
        { asset_id: "saved", reference_radius_m: 20 },
        { asset_id: "missing", reference_radius_m: 10 },
        { asset_id: "new", reference_radius_m: 3 },
        { asset_id: "fixed", reference_radius_m: 4 },
      ],
    )[0].candidates;
    assert.deepEqual(
      value.map((candidate) => candidate.reference_radius_m),
      [2, null, 3, 4],
    );
    assert.deepEqual(
      value.map((candidate) => candidate.scale),
      [
        { mode: "fixed", value: 0.9 },
        { mode: "fixed", value: 1 },
        { mode: "uniform_random", min: 0.8, max: 1.4 },
        { mode: "fixed", value: 1.2 },
      ],
    );
  });
  await test("reference ranges update separately, radius-only edits make no network calls", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    calls.length = 0;
    const geometry = ref({
      ...operation("g", "succeeded", 2).result,
      point_ids: ["one", "two"],
    });
    const appearance = ref([
      {
        role_key: "tree",
        candidates: [
          {
            asset_id: "known",
            weight: 1,
            scale: { mode: "fixed", value: 1.5 },
            reference_radius_m: 2,
          },
          {
            asset_id: "unknown",
            weight: 1,
            scale: { mode: "fixed", value: 1 },
            reference_radius_m: null,
          },
        ],
      },
    ]);
    let radii;
    respond = (request) => {
      assert.equal(request.path, "/api/v1/asset-assignment-previews");
      assert.ok(!request.body.includes("reference_radius"));
      return {
        choices: [
          [0, JSON.parse(request.body).roles[0].candidates[0].scale.value],
          [1, 1],
        ],
      };
    };
    const app = renderer.createApp({
      setup() {
        radii = useExampleReferenceRanges(
          () => geometry.value,
          () => appearance.value,
          () => 17,
        );
        return () => null;
      },
    });
    app.mount({});
    await tick(t);
    assert.deepEqual([...radii.value], [3, null]);
    const count = calls.length;
    appearance.value[0].candidates[0].reference_radius_m = 4;
    await flush();
    await tick(t);
    assert.deepEqual([...radii.value], [6, null]);
    assert.equal(calls.length, count);
    appearance.value[0].candidates[0].scale.value = 2;
    await flush();
    await tick(t);
    assert.deepEqual([...radii.value], [8, null]);
    assert.equal(calls.length, count + 1);
    appearance.value[0].candidates[0].reference_radius_m = null;
    await flush();
    await tick(t);
    assert.deepEqual([...radii.value], []);
    app.unmount();
  });
  await test("late assignment responses and unmounted views cannot change reference ranges", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const geometry = ref({
      ...operation("g", "succeeded").result,
      point_ids: ["one"],
    });
    const appearance = ref([
      {
        role_key: "tree",
        candidates: [
          {
            asset_id: "a",
            weight: 1,
            scale: { mode: "fixed", value: 1 },
            reference_radius_m: 2,
          },
        ],
      },
    ]);
    let release, radii;
    respond = (request) =>
      JSON.parse(request.body).roles[0].candidates[0].scale.value === 1
        ? new Promise((resolve) => {
            release = resolve;
          })
        : { choices: [[0, 2]] };
    const app = renderer.createApp({
      setup() {
        radii = useExampleReferenceRanges(
          () => geometry.value,
          () => appearance.value,
          () => 17,
        );
        return () => null;
      },
    });
    app.mount({});
    await tick(t);
    appearance.value[0].candidates[0].scale.value = 2;
    await flush();
    await tick(t);
    assert.deepEqual([...radii.value], [4]);
    release({ choices: [[0, 1]] });
    await flush();
    assert.deepEqual([...radii.value], [4]);
    appearance.value[0].candidates[0].scale.value = 1;
    await flush();
    await tick(t);
    app.unmount();
    release({ choices: [[0, 1]] });
    await flush();
    assert.deepEqual([...radii.value], []);
  });
  await test("strategy examples coalesce inputs and never send run seeds or geometry", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    calls.length = 0;
    respond = async (request) =>
      operation("current", request.method === "POST" ? "pending" : "succeeded");
    const panel = mount(null);
    try {
      await tick(t);
      assert.equal(calls.length, 0);
      for (let i = 1; i <= 20; i++) panel.source.value = input(i);
      await flush();
      await tick(t);
      const posts = calls.filter((call) => call.method === "POST");
      assert.equal(posts.length, 1);
      assert.deepEqual(JSON.parse(posts[0].body), input(20));
      assert.equal(panel.state.result.value.points.length, 1);
      assert.equal(panel.state.updating.value, false);
    } finally {
      panel.unmount();
    }
  });
  await test("late submit is cancelled after its input was replaced", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    calls.length = 0;
    let release;
    respond = (request) =>
      request.method === "POST" &&
      JSON.parse(request.body).strategy_parameters.spacing === 3
        ? new Promise((resolve) => {
            release = resolve;
          })
        : operation("new", "succeeded", 2);
    const panel = mount();
    try {
      await tick(t);
      panel.source.value = input(4);
      await flush();
      release(operation("old"));
      await flush();
      assert.ok(
        calls.some(
          (call) => call.method === "DELETE" && call.path.endsWith("/old"),
        ),
      );
      await tick(t);
      assert.equal(panel.state.result.value.points.length, 2);
      assert.ok(
        !calls.some((call) => !call.method && call.path.endsWith("/old")),
      );
    } finally {
      panel.unmount();
    }
  });
  await test("late polling cannot replace a newer picture", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    calls.length = 0;
    let release;
    respond = (request) => {
      if (request.method === "POST")
        return operation(
          JSON.parse(request.body).strategy_parameters.spacing === 3
            ? "old"
            : "new",
        );
      if (request.method === "DELETE") return operation("old", "cancelled");
      if (request.path.endsWith("/old"))
        return new Promise((resolve) => {
          release = resolve;
        });
      return operation("new", "succeeded", 2);
    };
    const panel = mount();
    try {
      await tick(t);
      panel.source.value = input(4);
      await flush();
      await tick(t);
      assert.equal(panel.state.result.value.points.length, 2);
      release(operation("old", "succeeded", 9));
      await flush();
      assert.equal(panel.state.result.value.points.length, 2);
    } finally {
      panel.unmount();
    }
  });
  await test("unmount cancels even a not-yet-returned submission and stops polling", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    calls.length = 0;
    let release;
    respond = (request) =>
      request.method === "POST"
        ? new Promise((resolve) => {
            release = resolve;
          })
        : operation("late", "cancelled");
    const panel = mount();
    await tick(t);
    panel.unmount();
    release(operation("late"));
    await flush();
    await tick(t);
    assert.ok(calls.some((call) => call.method === "DELETE"));
    assert.equal(calls.filter((call) => !call.method).length, 0);
  });
  await test("preview failure retains the last picture and can be retried", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    calls.length = 0;
    let failing = false;
    respond = (request) =>
      request.method === "POST"
        ? operation("current")
        : operation("current", failing ? "failed" : "succeeded");
    const panel = mount();
    try {
      await tick(t);
      const first = panel.state.result.value;
      failing = true;
      panel.source.value = input(4);
      await flush();
      await tick(t);
      assert.equal(panel.state.result.value, first);
      assert.ok(panel.state.error.value);
      failing = false;
      panel.state.refresh();
      await tick(t);
      assert.equal(panel.state.error.value, "");
      assert.equal(panel.state.updating.value, false);
    } finally {
      panel.unmount();
    }
  });
} finally {
  await server.close();
}
