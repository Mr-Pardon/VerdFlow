import type { ElevationBatch } from "../types";
import { asRecord } from "./runtime";

export function samplingWindows(lines: unknown[], batches: ElevationBatch[], size = 1024) {
  if (!Number.isFinite(size) || size <= 0) throw new Error("采样窗口大小无效");
  type Group = { region_id: string; name: string; indices: number[] };
  const cells = new Map<string, { x: number; y: number; bbox: number[]; groups: Map<string, Group> }>();
  for (const batch of batches) {
    for (let i = batch.start; i < batch.start + batch.count; i++) {
      const line = asRecord(lines[i]);
      const start = line?.start;
      const end = line?.end;
      if (!Array.isArray(start) || !Array.isArray(end) || start.length !== 3 || end.length !== 3
        || ![...start, ...end].every(v => typeof v === "number" && Number.isFinite(v))) {
        throw new Error("采样点坐标无效");
      }
      const x = Math.floor(start[0] / size), y = Math.floor(start[1] / size);
      const z = start[2] / 2 + end[2] / 2;
      const key = `${x}:${y}`;
      let cell = cells.get(key);
      if (!cell) {
        cell = { x, y, bbox: [start[0], start[1], z, start[0], start[1], z], groups: new Map() };
        cells.set(key, cell);
      }
      // The grid groups work; camera framing uses only its occupied extent.
      cell.bbox[0] = Math.min(cell.bbox[0]!, start[0]);
      cell.bbox[1] = Math.min(cell.bbox[1]!, start[1]);
      cell.bbox[2] = Math.min(cell.bbox[2]!, z);
      cell.bbox[3] = Math.max(cell.bbox[3]!, start[0]);
      cell.bbox[4] = Math.max(cell.bbox[4]!, start[1]);
      cell.bbox[5] = Math.max(cell.bbox[5]!, z);
      let group = cell.groups.get(batch.region_id);
      if (!group) { group = { region_id: batch.region_id, name: batch.name, indices: [] }; cell.groups.set(batch.region_id, group); }
      group.indices.push(i);
    }
  }
  return [...cells.values()].sort((a, b) => a.y - b.y || (a.y % 2 === 0 ? a.x - b.x : b.x - a.x))
    .map(cell => ({ bbox: cell.bbox, groups: [...cell.groups.values()] }));
}

export const intersectionRows = (input: unknown, count: number): unknown[] => {
  let value = input;
  for (let depth = 0; depth < 6 && !Array.isArray(value); depth += 1) {
    if (typeof value === "string") value = JSON.parse(value);
    else {
      const object = asRecord(value);
      if (!object) break;
      // Explorer returns a bare hit object for a one-ray request, alongside
      // result: 0 status metadata. Normalize it before unwrapping result/data.
      if (count === 1 && ("LineIntersectPoint" in object || "lineIntersectPoint" in object)) {
        value = [object];
        break;
      }
      const key = ["intersects", "LineIntersectPoints", "items", "result", "data"].find(key => object[key] !== undefined);
      if (!key) break;
      value = object[key];
    }
  }
  if (!Array.isArray(value) || value.length !== count) throw new Error(`高程返回数量与采样点数不一致（请求 ${count}，返回 ${Array.isArray(value) ? value.length : "非数组"}）`);
  return value.map(row => {
    const hit = asRecord(Array.isArray(row) ? row[0] : row);
    const position = hit?.LineIntersectPoint ?? hit?.lineIntersectPoint;
    if (!Array.isArray(position) || position.length !== 3 || !position.every(v => typeof v === "number" && Number.isFinite(v))) {
      throw new Error("部分点位未命中地形，请定位到该面后重试");
    }
    return { LineIntersectPoint: position };
  });
};
