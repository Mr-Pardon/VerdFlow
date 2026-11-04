import type { LiveRegion, LiveGeometry } from "../types";
export const geometryId = (geometry: LiveGeometry): string => geometry.path_id ?? geometry.region_id!;
export const geometryKind = (geometry: LiveGeometry) => geometry.path_id ? "polyline" : "polygon";
export const selectionId = (value: {region_id?: string | null; path_id?: string | null}): string => value.path_id ?? value.region_id ?? "";
export const geometryPayload = (geometry: LiveGeometry): LiveGeometry => geometry.path_id
  ? {path_id: geometry.path_id, name: geometry.name, coordinates: geometry.coordinates, bbox: geometry.bbox}
  : regionPayload(geometry as LiveRegion);

export const MAX_LIVE_VERTICES = 200_000;

export const regionPayload = ({ region_id, name, coordinates, bbox, holes }: LiveRegion): LiveRegion =>
  ({ region_id, name, coordinates, bbox, ...(holes?.length ? { holes } : {}) });

export function assertRegionBudget(regions: LiveGeometry[]) {
  const vertices = regions.reduce((sum, region) => sum + region.coordinates.length +
    (region.holes ?? []).reduce((count, ring) => count + ring.length, 0), 0);
  if (vertices > MAX_LIVE_VERTICES) {
    throw new Error(`合并后的选区累计 ${vertices.toLocaleString()} 个顶点（含孔洞），超过实时路径 200,000 个上限。请减少现有选区，或改用工作台的 SHP＋TIF 路径；本次未追加选区。`);
  }
  if (new TextEncoder().encode(JSON.stringify(regions.map(geometryPayload))).byteLength > 30 * 1024 * 1024) {
    throw new Error("合并后的选区数据超过实时路径 30 MiB 上限。请减少现有选区，或改用工作台的 SHP＋TIF 路径；本次未追加选区。");
  }
}
