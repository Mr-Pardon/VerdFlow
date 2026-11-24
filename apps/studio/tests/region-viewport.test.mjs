import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";
import { createServer } from "vite";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  configFile: false,
  cacheDir: fileURLToPath(new URL("../../../tests/.tmp/region-viewport-vite", import.meta.url)),
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true, hmr: false, ws: false },
});
const { createRegionViewportReader } = await server.ssrLoadModule("/src/features/dts/regionViewport.ts");
test.after(() => server.close());

const box = (min, max) => ({ min_x: min, min_y: min, min_z: 0, max_x: max, max_y: max, max_z: 0 });
const region = (id, classification, bounds, mode = "detail") => ({
  region_id: id, classification, bounds, boundary_mode: mode,
});
const request = client => ({
  client, sessionId: "project", regionSetId: "regions", regionCount: 2,
  bounds: box(0, 20), classifications: [], isCurrent: () => true,
});

test("complete exact geometry is reused for pan, zoom and classification", async () => {
  const reader = createRegionViewportReader();
  const green = region("a", "green", box(0, 4));
  const street = region("b", "street", box(8, 12));
  let requests = 0;
  const input = request({
    regionGeometry: async args => {
      requests++;
      assert.equal(args.simplificationTolerance, 0);
      return { items: [green, street], truncated: false };
    },
  });
  assert.deepEqual((await reader.read(input)).items, [green, street]);
  assert.deepEqual((await reader.read({ ...input, bounds: box(6, 15) })).items, [street]);
  assert.deepEqual((await reader.read({ ...input, classifications: ["green"] })).items, [green]);
  assert.equal(requests, 1);
});

test("a closer viewport can switch from overview to original vectors", async () => {
  const reader = createRegionViewportReader();
  const street = region("b", "street", box(8, 12));
  let requests = 0;
  const input = request({
    regionGeometry: async () => {
      requests++;
      return requests === 1
        ? { items: [], truncated: true, overview_recommended: true }
        : { items: [street], truncated: false };
    },
    regionOverview: async ({ bounds }) => sampleOverview(bounds),
  });
  assert.ok((await reader.read(input)).overview);
  const detail = await reader.read({ ...input, bounds: box(8, 11) });
  assert.deepEqual(detail.items, [street]);
  assert.equal(detail.overview, null);
  assert.equal(requests, 2);
});

test("dense viewport loads one overview without following vector pages", async () => {
  const reader = createRegionViewportReader();
  const calls = [];
  const input = request({
    regionGeometry: async () => {
      calls.push("geometry");
      return { items: [], truncated: true, overview_recommended: true };
    },
    regionOverview: async ({ bounds }) => {
      calls.push("overview");
      return sampleOverview(bounds);
    },
  });
  const result = await reader.read(input);
  assert.deepEqual(calls, ["geometry", "overview"]);
  assert.equal(result.overview.pixels[0], 1);
  assert.deepEqual(result.items, []);
});

function sampleOverview(bounds) {
  return {
    bounds, width: 1, height: 1, items: [["region", "green", 5]],
    pixels: deflateSync(Buffer.from([1, 0, 0, 0])).toString("base64"),
  };
}
