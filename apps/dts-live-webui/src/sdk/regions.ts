import type { LiveRegion } from "../types";
import { asRecord } from "./runtime";

export const previewFrameThickness = ({ bbox }: Pick<LiveRegion, "bbox">): number => {
  const shortSide = Math.min(bbox[3]! - bbox[0]!, bbox[4]! - bbox[1]!);
  return Math.min(1, Math.max(0.01, shortSide * 0.004));
};

export const findCoordinates = (value: unknown, depth = 4): number[][] | null => {
  if (typeof value === "string") {
    try {
      return findCoordinates(JSON.parse(value), depth);
    } catch {
      return null;
    }
  }
  const direct = normalizeCoordinates(value);
  if (direct || depth <= 0) return direct;
  const record = asRecord(value);
  if (!record) return null;
  for (const key of [
    "coordinates",
    "points",
    "positions",
    "vertices",
    "vertexes",
    "pointList",
    "positionList",
    "data",
    "result",
    "payload",
    "value",
  ]) {
    if (record[key] === undefined) continue;
    const nested = findCoordinates(record[key], depth - 1);
    if (nested) return nested;
  }
  return null;
};

export const normalizeCoordinates = (value: unknown): number[][] | null => {
  if (!Array.isArray(value)) return null;
  if (value.length >= 9 && value.length % 3 === 0 && typeof value[0] === "number") {
    const result: number[][] = [];
    for (let index = 0; index < value.length; index += 3) {
      const point = numericPoint(value.slice(index, index + 3));
      if (!point) return null;
      result.push(point);
    }
    return result;
  }
  const rows =
    value.length === 1 && Array.isArray(value[0]) && Array.isArray(value[0][0])
      ? value[0]
      : value;
  const result = rows.map(numericPoint);
  return result.length >= 3 && result.every(Boolean) ? (result as number[][]) : null;
};

export const numericPoint = (value: unknown): number[] | null => {
  const record = asRecord(value);
  const raw = Array.isArray(value)
    ? value.slice(0, 3)
    : record
      ? [record.x ?? record.X, record.y ?? record.Y, record.z ?? record.Z]
      : [];
  if (raw.length !== 3) return null;
  const point = raw.map(Number);
  return point.every(Number.isFinite) ? point : null;
};

export const createLiveRegion = (input: number[][], ordinal: number): LiveRegion => {
  const coordinates = input.map(point => [...point]);
  if (coordinates.length > 1 && coordinates[0]!.every((v, i) => v === coordinates.at(-1)![i])) coordinates.pop();
  if (coordinates.length < 3) throw new Error("选区至少需要三个顶点");
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  let twiceArea = 0;
  const origin = coordinates[0]!;
  coordinates.forEach((point, index) => {
    if (point.length !== 3 || !point.every(Number.isFinite)) throw new Error("选区包含无效坐标");
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis]!, point[axis]!);
      max[axis] = Math.max(max[axis]!, point[axis]!);
    }
    const next = coordinates[(index + 1) % coordinates.length]!;
    twiceArea += (point[0]! - origin[0]!) * (next[1]! - origin[1]!) - (next[0]! - origin[0]!) * (point[1]! - origin[1]!);
  });
  if (!Number.isFinite(twiceArea) || Math.abs(twiceArea) <= 1e-10) throw new Error("选区面积必须是大于零的有限值");
  return {
    region_id: `live_${crypto.randomUUID()}`,
    name: `选区 ${ordinal}`,
    coordinates,
    bbox: [...min, ...max],
  };
};
