import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createServer } from "vite";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)), configFile: false,
  cacheDir: fileURLToPath(new URL("../../../tests/.tmp/imagery-index-vite", import.meta.url)),
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true, hmr: false, ws: false },
});
after(() => server.close());
const { areaOrder, createLegacyReviewIndex, decodeRegionBits, encodeRegionBits, hasRegion, loadPackedReviewIndex, markRegion }
  = await server.ssrLoadModule("/src/features/dts/imageryReviewIndex.ts");

const count = 32_771;
const first = { review_index_encoding: "ordinal-f64-u32-le/1", region_count: count, page_count: 3 };
function batch(start) {
  const rows = Math.min(count - start, 32_768);
  const bytes = Buffer.alloc(rows * 12);
  for (let i = 0; i < rows; i++) {
    bytes.writeDoubleLE(start === 0 ? 20 : 2 ** 32 + i, i * 12);
    bytes.writeUInt32LE(start === 0 ? 1 : 3, i * 12 + 8);
  }
  return { start, count: rows, rows_base64: bytes.toString("base64") };
}

test("packed rows preserve full pixel counts and the final candidate ordinal", async () => {
  const calls = [];
  const index = await loadPackedReviewIndex(first, async start => { calls.push(start); return batch(start); }, () => false);
  assert.deepEqual(calls, [0, 32_768]);
  assert.deepEqual(index.itemAt(count - 1), { region_id: "region_32771", label: "区域 32771", pixel_count: 2 ** 32 + 2, page: 3 });
  assert.equal(index.ordinalOf("region_32771"), count - 1);
  assert.equal(index.ordinalOf("region_032771"), -1);
  assert.deepEqual([...areaOrder(index).subarray(0, 4)], [32_770, 32_769, 32_768, 0]);
});

test("incomplete and closed index reads cannot publish partial candidates", async () => {
  await assert.rejects(loadPackedReviewIndex(first, async start => ({ ...batch(start), count: 1 }), () => false));
  let calls = 0;
  const index = await loadPackedReviewIndex(first, async start => { calls++; return batch(start); }, () => true);
  assert.equal(calls, 1);
  assert.equal(index, null);
  const legacy = createLegacyReviewIndex([{ region_id: "legacy", label: "旧区域", pixel_count: null, page: 1 }], 1);
  assert.equal(legacy.ordinalOf("legacy"), 0);
  assert.equal(legacy.pixelsAt(0), null);
});

test("selection bitmap round trips across byte boundaries and rejects trailing bits", () => {
  const bits = new Uint8Array(Math.ceil(count / 8));
  for (const ordinal of [0, 7, 8, count - 1]) markRegion(bits, ordinal);
  const recovered = decodeRegionBits(encodeRegionBits(bits), count);
  assert.deepEqual(recovered, bits);
  assert.equal(hasRegion(recovered, count - 1), true);
  assert.equal(hasRegion(recovered, count - 2), false);
  markRegion(bits, count);
  assert.throws(() => decodeRegionBits(encodeRegionBits(bits), count));
});
