import type { Bounds3D, LocalApiClient, RegionOverview, RegionOverviewItem, RegionViewerItem } from "../../api";

const PAGE_SIZE = 5000;
const MAX_PAGES = 4;
const MAX_OVERVIEW_SIDE = 1024;

export interface RegionOverviewGrid extends Omit<RegionOverview, "pixels" | "items"> {
  pixels: Uint32Array;
  items: RegionOverviewItem[];
}

export interface RegionViewport {
  items: RegionViewerItem[];
  overview: RegionOverviewGrid | null;
}

export async function readRegionViewport(input: {
  client: LocalApiClient;
  sessionId: string;
  regionSetId: string;
  bounds: Bounds3D;
  classifications: string[];
  isCurrent: () => boolean;
}): Promise<RegionViewport | undefined> {
  const { client, sessionId, regionSetId, bounds, classifications, isCurrent } = input;
  const items: RegionViewerItem[] = [];
  let afterOrdinal = -1;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const chunk = await client.regionGeometry({
      sessionId, regionSetId, classifications, afterOrdinal, limit: PAGE_SIZE,
      minX: bounds.min_x, minY: bounds.min_y, maxX: bounds.max_x, maxY: bounds.max_y,
      overviewThreshold: PAGE_SIZE * MAX_PAGES,
    });
    if (!isCurrent()) return;
    if (chunk.overview_recommended || (chunk.truncated && page === MAX_PAGES - 1)) {
      const packed = await client.regionOverview({ sessionId, regionSetId, bounds, classifications });
      if (!isCurrent()) return;
      return { items: [], overview: await decodeOverview(packed) };
    }
    items.push(...chunk.items);
    if (!chunk.truncated || chunk.next_after_ordinal === null) return { items, overview: null };
    if (chunk.next_after_ordinal <= afterOrdinal) throw new Error("区域预览响应顺序无效");
    afterOrdinal = chunk.next_after_ordinal;
  }
  return { items, overview: null };
}

async function decodeOverview(input: RegionOverview): Promise<RegionOverviewGrid> {
  const { width, height, pixels, items, bounds } = input;
  if (![width, height].every(value => Number.isInteger(value) && value > 0 && value <= MAX_OVERVIEW_SIDE)
      || pixels.length > 6 * 1024 ** 2 || items.length > width * height) {
    throw new Error("区域概览数据无效");
  }
  const bytes = Uint8Array.from(atob(pixels), character => character.charCodeAt(0));
  const reader = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate")).getReader();
  const output = new Uint8Array(width * height * 4);
  let offset = 0;
  try {
    for (let chunk = await reader.read(); !chunk.done; chunk = await reader.read()) {
      if (offset + chunk.value.length > output.length) throw new Error("区域概览数据长度无效");
      output.set(chunk.value, offset);
      offset += chunk.value.length;
    }
  } finally {
    await reader.cancel();
  }
  if (offset !== output.length) throw new Error("区域概览数据长度无效");
  const source = new DataView(output.buffer);
  const grid = new Uint32Array(width * height);
  for (let i = 0; i < grid.length; i += 1) {
    const index = source.getUint32(i * 4, true);
    if (index > items.length) throw new Error("区域概览索引无效");
    grid[i] = index;
  }
  return {
    bounds, width, height, pixels: grid,
    items: items.map(([region_id, classification, footprint_area_xy]) =>
      ({ region_id, classification, footprint_area_xy })),
  };
}

export function createRegionOverviewCanvas(
  grid: RegionOverviewGrid,
  selected: ReadonlySet<string>,
  committed: ReadonlySet<string>,
  mode: "regions" | "planted" | "context",
): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = grid.width;
  canvas.height = grid.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("区域概览画布不可用");
  const image = context.createImageData(canvas.width, canvas.height);
  const palette = new Uint32Array(grid.items.length + 1);
  const colors = new Uint8Array(palette.buffer);
  grid.items.forEach((item, i) => {
    const opacity = mode === "regions" ? 174 : 122;
    colors.set(selected.has(item.region_id) ? [225, 82, 69, 194]
      : committed.has(item.region_id) && mode === "regions" ? [92, 107, 104, 157]
      : committed.has(item.region_id) && mode === "planted" ? [46, 143, 122, 188]
      : item.classification?.includes("street") ? [225, 185, 99, opacity] : [91, 170, 148, opacity], (i + 1) * 4);
  });
  const rgba = new Uint32Array(image.data.buffer);
  for (let i = 0; i < rgba.length; i += 1) rgba[i] = palette[grid.pixels[i]];
  context.putImageData(image, 0, 0);
  return canvas;
}

export function pickRegionOverview(grid: RegionOverviewGrid, x: number, y: number): string | undefined {
  const { bounds, width, height, pixels, items } = grid;
  const col = Math.floor((x - bounds.min_x) / (bounds.max_x - bounds.min_x) * width);
  const row = Math.floor((bounds.max_y - y) / (bounds.max_y - bounds.min_y) * height);
  if (col < 0 || col >= width || row < 0 || row >= height) return;
  const slot = pixels[row * width + col];
  if (!slot) return;
  return items[slot - 1]?.region_id;
}

export function drawRegionOverview(
  context: CanvasRenderingContext2D,
  raster: HTMLCanvasElement,
  bounds: Bounds3D,
  worldToScreen: (x: number, y: number) => { x: number; y: number },
) {
  const origin = worldToScreen(bounds.min_x, bounds.max_y);
  const right = worldToScreen(bounds.max_x, bounds.max_y);
  const bottom = worldToScreen(bounds.min_x, bounds.min_y);
  context.save();
  context.imageSmoothingEnabled = false;
  context.transform(
    (right.x - origin.x) / raster.width, (right.y - origin.y) / raster.width,
    (bottom.x - origin.x) / raster.height, (bottom.y - origin.y) / raster.height,
    origin.x, origin.y,
  );
  context.drawImage(raster, 0, 0);
  context.restore();
}
