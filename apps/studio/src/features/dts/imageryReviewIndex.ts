import type { ImageryCandidate, ImageryReviewIndexBatch, ImageryReviewItem } from "../../api";

const ENCODING = "ordinal-f64-u32-le/1";
const ROW_BYTES = 12;
const CHUNK_ROWS = 32_768;
const labelOrder = new Intl.Collator("zh-CN", { numeric: true });

export interface ImageryReviewIndex {
  length: number;
  packed: boolean;
  at: (ordinal: number) => string;
  ordinalOf: (id: string) => number;
  pixelsAt: (ordinal: number) => number | null;
  itemAt: (ordinal: number) => ImageryReviewItem;
}

const canonicalId = (ordinal: number) => `region_${String(ordinal + 1).padStart(5, "0")}`;

export const createLegacyReviewIndex = (items: ImageryReviewItem[], count: number): ImageryReviewIndex => {
  const ordinals = new Map(items.map((item, ordinal) => [item.region_id, ordinal]));
  if (items.length !== count || ordinals.size !== count) throw new Error("候选区域索引不完整。");
  return {
    length: count, packed: false, at: ordinal => items[ordinal].region_id,
    ordinalOf: id => ordinals.get(id) ?? -1,
    pixelsAt: ordinal => items[ordinal].pixel_count, itemAt: ordinal => items[ordinal],
  };
};

export async function loadPackedReviewIndex(
  first: ImageryCandidate, fetchBatch: (start: number) => Promise<ImageryReviewIndexBatch>, disposed: () => boolean,
): Promise<ImageryReviewIndex | null> {
  const length = first.region_count;
  if (first.review_index_encoding !== ENCODING || !Number.isSafeInteger(length) || length <= 0 || length >= 2 ** 24) {
    throw new Error("候选区域索引格式无效。");
  }
  const rows = new Uint8Array(length * ROW_BYTES);
  const view = new DataView(rows.buffer);
  for (let start = 0; start < length;) {
    const count = Math.min(CHUNK_ROWS, length - start);
    const batch = await fetchBatch(start);
    if (disposed()) return null;
    if (batch.start !== start || batch.count !== count || batch.rows_base64.length !== Math.ceil(count * ROW_BYTES / 3) * 4) {
      throw new Error("候选区域索引不完整。");
    }
    const bytes = atob(batch.rows_base64);
    if (bytes.length !== count * ROW_BYTES) throw new Error("候选区域索引不完整。");
    const offset = start * ROW_BYTES;
    for (let i = 0; i < bytes.length; i++) rows[offset + i] = bytes.charCodeAt(i);
    for (let i = start; i < start + count; i++) {
      const pixels = view.getFloat64(i * ROW_BYTES, true);
      const page = view.getUint32(i * ROW_BYTES + 8, true);
      if (!Number.isSafeInteger(pixels) || pixels <= 0 || page < 1 || page > first.page_count) {
        throw new Error("候选区域索引内容无效。");
      }
    }
    start += count;
  }
  const pixelsAt = (ordinal: number) => view.getFloat64(ordinal * ROW_BYTES, true);
  return {
    length, packed: true, at: canonicalId, pixelsAt,
    ordinalOf(id) {
      if (!id.startsWith("region_")) return -1;
      const ordinal = Number(id.slice(7)) - 1;
      return Number.isInteger(ordinal) && ordinal >= 0 && ordinal < length && canonicalId(ordinal) === id ? ordinal : -1;
    },
    itemAt: ordinal => ({
      region_id: canonicalId(ordinal), label: `区域 ${ordinal + 1}`,
      pixel_count: pixelsAt(ordinal), page: view.getUint32(ordinal * ROW_BYTES + 8, true),
    }),
  };
}

export const areaOrder = (index: ImageryReviewIndex) => {
  const ordinals = new Uint32Array(index.length);
  for (let i = 0; i < ordinals.length; i++) ordinals[i] = i;
  return ordinals.sort((a, b) => (index.pixelsAt(b) ?? -1) - (index.pixelsAt(a) ?? -1)
    || (index.packed ? a - b : labelOrder.compare(index.at(a), index.at(b))));
};

export const hasRegion = (bits: Uint8Array, ordinal: number) => Boolean(bits[ordinal >> 3] & (1 << (ordinal & 7)));
export const markRegion = (bits: Uint8Array, ordinal: number) => { bits[ordinal >> 3] |= 1 << (ordinal & 7); };

export const decodeRegionBits = (encoded: string, count: number) => {
  const bytes = atob(encoded);
  if (bytes.length !== Math.ceil(count / 8) || (count % 8 && bytes.charCodeAt(bytes.length - 1) >> (count % 8))) {
    throw new Error("已确认范围与候选索引不一致。");
  }
  return Uint8Array.from(bytes, byte => byte.charCodeAt(0));
};

export const encodeRegionBits = (bits: Uint8Array) => {
  const parts: string[] = [];
  for (let start = 0; start < bits.length; start += 8192) {
    parts.push(String.fromCharCode(...bits.subarray(start, start + 8192)));
  }
  return btoa(parts.join(""));
};
