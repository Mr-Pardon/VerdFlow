import type { Bounds3D, ImageryCandidate, ImageryTile, ImageryTileResponse } from "../../api";
import { createCandidateSurface, decodeImageryPng, readImageryIds, toPixel } from "./imageryReviewMap";

export type ImageryDetailTile = ReturnType<typeof createCandidateSurface> & {
  backdrop: HTMLImageElement;
  transform: ImageryTile["transform"];
  refined: boolean;
  refinementUnavailable: boolean;
};

const TILE_SIZE = 512;
const CACHE_LIMIT = 32;
const VIEW_LIMIT = 20;
type TileKey = { key: string; level: number; column: number; row: number };

export function createImageryDetail(
  grid: NonNullable<ImageryCandidate["source_grid"]>, preview: ImageryCandidate["preview_transform"],
  fetchTile: (level: number, column: number, row: number) => Promise<ImageryTileResponse>,
  state: (id: string) => 0 | 1 | 2,
  publish: (tiles: ImageryDetailTile[]) => void,
  setRefinementPending: (pending: boolean) => void,
) {
  const project = toPixel(grid.transform);
  const resolution = (transform: ImageryTile["transform"]) => Math.max(Math.hypot(transform[0], transform[3]), Math.hypot(transform[1], transform[4]));
  const sourceResolution = resolution(grid.transform);
  const previewResolution = resolution(preview);
  const cache = new Map<string, { tile: ImageryDetailTile; palette: number; revision: number }>();
  let wanted: TileKey[] = [];
  let active = new Set<string>();
  let attempted = new Set<string>();
  let palette = 0;
  let running = false;
  let disposed = false;
  let revision = 0;
  let retry: ReturnType<typeof setTimeout> | undefined;

  const cancelRetry = () => {
    if (retry !== undefined) clearTimeout(retry);
    retry = undefined;
  };

  const draw = () => {
    const visible = new Map<string, ImageryDetailTile>();
    const add = (key: string) => {
      const entry = cache.get(key);
      if (!entry) return false;
      cache.delete(key);
      cache.set(key, entry);
      const current = entry.revision === revision;
      if (entry.palette !== palette) {
        entry.tile.paint(state, undefined, current);
        entry.palette = palette;
      }
      visible.set(key, current ? entry.tile : { ...entry.tile, refined: false, refinementUnavailable: false });
      return true;
    };
    // Use an already loaded parent until the sharper child arrives.
    for (const item of wanted) {
      if (cache.has(item.key)) continue;
      for (let level = item.level + 1; level <= 16 && sourceResolution * 2 ** level < previewResolution; level++) {
        const factor = 2 ** (level - item.level);
        if (add(`${level}/${Math.floor(item.column / factor)}/${Math.floor(item.row / factor)}`)) break;
      }
    }
    for (const item of wanted) add(item.key);
    publish([...visible.values()].sort((a, b) => resolution(b.transform) - resolution(a.transform)));
  };

  const load = async () => {
    if (running || disposed || retry !== undefined) return;
    running = true;
    try {
      while (!disposed) {
        const next = wanted.find(item => cache.get(item.key)?.revision !== revision && !attempted.has(item.key));
        if (!next) break;
        attempted.add(next.key);
        try {
          const requested = revision;
          const result = await fetchTile(next.level, next.column, next.row);
          if (!result || disposed || requested !== revision || !active.has(next.key)) continue;
          if ("retry_after_ms" in result) {
            attempted.delete(next.key);
            setRefinementPending(result.refinement_pending === true);
            const delay = Number.isFinite(result.retry_after_ms) ? Math.max(100, Math.min(1500, result.retry_after_ms)) : 350;
            retry = setTimeout(() => { retry = undefined; void load(); }, delay);
            break;
          }
          setRefinementPending(false);
          const { width, height, transform, region_ids } = result;
          if (![width, height].every(value => Number.isInteger(value) && value >= 1 && value <= TILE_SIZE)) continue;
          const [backdrop, labels, refinement] = await Promise.all([
            decodeImageryPng(result.base_png, width, height), decodeImageryPng(result.ids_png, width + 2, height + 2),
            result.refinement_png ? decodeImageryPng(result.refinement_png, width, height) : undefined,
          ]);
          if (disposed || requested !== revision || !active.has(next.key)) continue;
          const ids = readImageryIds(labels, width + 2, height + 2, region_ids.length);
          const proposed = refinement ? readImageryIds(refinement, width, height, 1) : undefined;
          const tile = { ...createCandidateSurface(ids, width, height, transform, region_ids, 1, proposed), transform, backdrop,
            refined: Boolean(proposed), refinementUnavailable: result.refinement_unavailable === true };
          cache.set(next.key, { tile, palette: -1, revision: requested });
          draw();
          while (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value!);
        } catch {
          // Detail is optional: the overview remains selectable when a tile cannot be read.
        }
      }
    } finally {
      running = false;
    }
  };

  const update = (bounds: Bounds3D, pixelsPerMeter: number) => {
    if (disposed) return;
    const corners = [project(bounds.min_x, bounds.min_y), project(bounds.min_x, bounds.max_y),
      project(bounds.max_x, bounds.min_y), project(bounds.max_x, bounds.max_y)];
    const x0 = Math.max(0, Math.min(...corners.map(([x]) => x)));
    const y0 = Math.max(0, Math.min(...corners.map(([, y]) => y)));
    const x1 = Math.min(grid.width, Math.max(...corners.map(([x]) => x)));
    const y1 = Math.min(grid.height, Math.max(...corners.map(([, y]) => y)));
    const next: TileKey[] = [];
    if (x1 > x0 && y1 > y0 && previewResolution * pixelsPerMeter > 1.15) {
      for (let level = Math.max(0, Math.floor(Math.log2(1 / (sourceResolution * pixelsPerMeter))));
        level <= 16 && (level === 0 || sourceResolution * 2 ** level < previewResolution); level++) {
        const span = TILE_SIZE * 2 ** level;
        const c0 = Math.floor(x0 / span), c1 = Math.ceil(x1 / span);
        const r0 = Math.floor(y0 / span), r1 = Math.ceil(y1 / span);
        if ((c1 - c0) * (r1 - r0) > VIEW_LIMIT) continue;
        for (let row = r0; row < r1; row++) for (let column = c0; column < c1; column++) {
          next.push({ key: `${level}/${column}/${row}`, level, column, row });
        }
        const distance = ({ column, row }: TileKey) => Math.hypot(column + 0.5 - (x0 + x1) / (2 * span), row + 0.5 - (y0 + y1) / (2 * span));
        next.sort((a, b) => distance(a) - distance(b));
        break;
      }
    }
    const previous = active;
    active = new Set(next.map(item => item.key));
    attempted = new Set([...attempted].filter(key => active.has(key) && previous.has(key)));
    wanted = next;
    if (!wanted.length) setRefinementPending(false);
    draw();
    void load();
  };

  return {
    update,
    refresh: () => {
      if (disposed) return;
      revision++;
      palette++;
      attempted.clear();
      cancelRetry();
      setRefinementPending(false);
      draw();
      void load();
    },
    suspend: () => { wanted = []; active.clear(); cancelRetry(); setRefinementPending(false); },
    repaint: () => { palette++; draw(); },
    close: () => { disposed = true; wanted = []; active.clear(); cache.clear(); cancelRetry(); },
  };
}
