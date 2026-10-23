import { asRecord } from "./runtime";

export type CameraPose = [number, number, number, number, number];

export function readCameraPose(value: unknown): CameraPose {
  if (typeof value === "string") value = JSON.parse(value);
  const record = asRecord(value);
  const pose = Array.isArray(record?.camera) ? record.camera.slice(0, 5)
    : [record?.x, record?.y, record?.z, record?.pitch, record?.yaw];
  if (pose.length !== 5 || !pose.every(v => typeof v === "number" && Number.isFinite(v))) {
    throw new Error("DTS 相机位置返回无效");
  }
  return pose as CameraPose;
}
