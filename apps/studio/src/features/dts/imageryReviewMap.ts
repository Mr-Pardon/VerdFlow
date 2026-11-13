import type { ImageryCandidate, ImageryReviewIndexBatch, ImageryReviewItem } from "../../api";
import { createLegacyReviewIndex, loadPackedReviewIndex, type ImageryReviewIndex } from "./imageryReviewIndex";

export const toPixel = ([a, b, c, d, e, f]: ImageryCandidate["preview_transform"]) => {
  const determinant = a * e - b * d;
  if (!Number.isFinite(determinant) || !determinant) throw new Error("影像预览坐标无效。");
  return (x: number, y: number) => [
    (e * (x - c) - b * (y - f)) / determinant,
    (a * (y - f) - d * (x - c)) / determinant,
  ] as const;
};

const surface = (width: number, height: number) => {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("无法创建影像预览。");
  return { canvas, context };
};

type CandidateRow = ImageryCandidate["regions"][number];

export async function decodeImageryPng(png: string, width: number, height: number) {
  const image = new Image();
  image.src = `data:image/png;base64,${png}`;
  await image.decode();
  if (image.naturalWidth !== width || image.naturalHeight !== height) throw new Error("候选叠图与底图尺寸不一致。");
  return image;
}

export function readImageryIds(image: CanvasImageSource, width: number, height: number, count: number) {
  const { context } = surface(width, height);
  context.drawImage(image, 0, 0);
  const rgba = context.getImageData(0, 0, width, height).data;
  const ids = new Uint32Array(width * height);
  for (let i = 0, p = 0; i < ids.length; i++, p += 4) {
    ids[i] = rgba[p] | (rgba[p + 1] << 8) | (rgba[p + 2] << 16);
    if (ids[i] > count) throw new Error("候选叠图索引不完整。");
  }
  return ids;
}

// Legacy jobs only have paged boundaries. Rasterize each page, then release its geometry.
const paintLegacyRegion = (
  ids: Uint32Array, context: CanvasRenderingContext2D, region: CandidateRow,
  ordinal: number, project: ReturnType<typeof toPixel>,
) => {
  if (!region.footprint_boundary_xyz.length) return;
  const { width, height } = context.canvas;
  const [x0, y0, , x1, y1] = region.bbox;
  const corners = [project(x0, y0), project(x1, y0), project(x0, y1), project(x1, y1)];
  const left = Math.max(0, Math.floor(Math.min(...corners.map(([x]) => x))));
  const right = Math.min(width, Math.ceil(Math.max(...corners.map(([x]) => x))));
  const top = Math.max(0, Math.floor(Math.min(...corners.map(([, y]) => y))));
  const bottom = Math.min(height, Math.ceil(Math.max(...corners.map(([, y]) => y))));
  if (left >= right || top >= bottom) return;
  const path = new Path2D();
  for (const ring of region.footprint_boundary_xyz) {
    if (!ring.length) continue;
    path.moveTo(...project(ring[0][0], ring[0][1]));
    for (let i = 1; i < ring.length; i++) path.lineTo(...project(ring[i][0], ring[i][1]));
    path.closePath();
  }
  context.clearRect(left, top, right - left, bottom - top);
  context.fill(path, "evenodd");
  const rgba = context.getImageData(left, top, right - left, bottom - top).data;
  let p = 3;
  for (let y = top; y < bottom; y++) {
    for (let x = left; x < right; x++, p += 4) {
      if (rgba[p] >= 128) ids[y * width + x] = ordinal;
    }
  }
};

export async function loadImageryReviewMap(
  first: ImageryCandidate, getPage: (page: number) => Promise<ImageryCandidate>,
  disposed: () => boolean, fetchIndex: (start: number) => Promise<ImageryReviewIndexBatch>,
) {
  const width = first.preview_width, height = first.preview_height;
  if (![width, height].every(value => Number.isInteger(value) && value >= 1 && value <= 1600)) {
    throw new Error("影像预览尺寸无效。");
  }
  const project = toPixel(first.preview_transform);
  let ids: Uint32Array;
  let index: ImageryReviewIndex;
  if (first.overview_png_base64 && (first.review_index || first.review_index_encoding)) {
    const [image, loadedIndex] = await Promise.all([
      decodeImageryPng(first.overview_png_base64, width, height),
      first.review_index_encoding ? loadPackedReviewIndex(first, fetchIndex, disposed)
        : createLegacyReviewIndex(first.review_index!, first.region_count),
    ]);
    if (disposed() || !loadedIndex) return null;
    index = loadedIndex;
    ids = readImageryIds(image, width, height, index.length);
  } else {
    ids = new Uint32Array(width * height);
    const scratch = surface(width, height).context;
    const items: ImageryReviewItem[] = [];
    for (let page = 1; page <= first.page_count; page++) {
      const batch = page === 1 ? first : await getPage(page);
      if (disposed()) return null;
      for (const region of batch.regions) {
        items.push({ region_id: region.region_id, label: region.label, pixel_count: region.pixel_count, page });
        paintLegacyRegion(ids, scratch, region, items.length, project);
      }
    }
    index = createLegacyReviewIndex(items, first.region_count);
  }
  return { index, ...createCandidateSurface(ids, width, height, first.preview_transform, index) };
}

export function createCandidateSurface(ids: Uint32Array, width: number, height: number,
  transform: ImageryCandidate["preview_transform"], keys: { length: number; at: (ordinal: number) => string | undefined }, padding = 0,
  proposed?: Uint32Array) {
  const project = toPixel(transform);
  const [a, b, c, d, e, f] = transform;
  const stride = width + padding * 2;
  const offset = padding * (stride + 1);
  const { canvas, context } = surface(width, height);
  const image = context.createImageData(width, height);
  const rgba = image.data;
  const palette = new Uint8Array(keys.length + 1);
  const paint = (state: (id: string) => 0 | 1 | 2, stateAt?: (ordinal: number) => 0 | 1 | 2, showProposed = true) => {
    for (let i = 0; i < keys.length; i++) palette[i + 1] = stateAt ? stateAt(i) : state(keys.at(i)!);
    for (let y = 0, p = 0; y < height; y++) for (let x = 0; x < width; x++, p += 4) {
      const i = offset + y * stride + x;
      const id = ids[i];
      const value = palette[id];
      if (!value) {
        rgba[p + 3] = 0;
        continue;
      }
      const edge = (!padding && (x === 0 || x === width - 1)) || id !== ids[i - 1] || id !== ids[i + 1]
        || id !== ids[i - stride] || id !== ids[i + stride];
      if (value === 1 && showProposed && proposed?.[y * width + x]) {
        rgba[p] = 255; rgba[p + 1] = 171; rgba[p + 2] = 57; rgba[p + 3] = 175;
        continue;
      }
      rgba[p] = value === 1 ? 46 : 231;
      rgba[p + 1] = value === 1 ? 212 : 174;
      rgba[p + 2] = value === 1 ? 155 : 105;
      rgba[p + 3] = value === 1 ? (edge ? 176 : 100) : (edge ? 228 : 0);
    }
    context.putImageData(image, 0, 0);
  };
  const covers = (worldX: number, worldY: number) => {
    const [x, y] = project(worldX, worldY);
    return x >= 0 && y >= 0 && x < width && y < height;
  };
  const pick = (worldX: number, worldY: number) => {
    const [x, y] = project(worldX, worldY);
    if (!(x >= 0 && y >= 0 && x < width && y < height)) return undefined;
    const id = ids[offset + Math.floor(y) * stride + Math.floor(x)];
    return palette[id] ? keys.at(id - 1) : undefined;
  };
  const pickArea = (corners: readonly { x: number; y: number }[], covered?: (x: number, y: number) => boolean) => {
    const points = corners.map(({ x, y }) => project(x, y));
    const [[ax, ay], [bx, by], , [dx, dy]] = points;
    const ux = bx - ax, uy = by - ay, vx = dx - ax, vy = dy - ay;
    const determinant = ux * vy - uy * vx;
    if (!determinant) return [];
    const left = Math.max(0, Math.floor(Math.min(...points.map(([x]) => x))));
    const right = Math.min(width, Math.ceil(Math.max(...points.map(([x]) => x))));
    const top = Math.max(0, Math.floor(Math.min(...points.map(([, y]) => y))));
    const bottom = Math.min(height, Math.ceil(Math.max(...points.map(([, y]) => y))));
    const found = new Set<number>();
    for (let y = top; y < bottom; y++) {
      const offsetY = y + 0.5 - ay;
      for (let x = left; x < right; x++) {
        const id = ids[offset + y * stride + x];
        if (!palette[id] || found.has(id)) continue;
        const offsetX = x + 0.5 - ax;
        const u = (offsetX * vy - offsetY * vx) / determinant;
        const v = (ux * offsetY - uy * offsetX) / determinant;
        if (u >= 0 && u <= 1 && v >= 0 && v <= 1 &&
          !covered?.(a * (x + 0.5) + b * (y + 0.5) + c, d * (x + 0.5) + e * (y + 0.5) + f)) found.add(id);
      }
    }
    return [...found].map(id => keys.at(id - 1)!);
  };
  return { canvas, covers, paint, pick, pickArea };
}
