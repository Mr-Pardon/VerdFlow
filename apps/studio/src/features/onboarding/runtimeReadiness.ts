import type { ExternalRuntimeInfo } from "../../api";
import type { GuideWorkflow } from "./steps";

export const requiredRuntimeIds = (workflow: GuideWorkflow) => workflow === "both" ? ["blender", "dts-explorer"] : [workflow === "fbx" ? "blender" : "dts-explorer"];

export function runtimeReadiness(runtimes: readonly ExternalRuntimeInfo[], workflow: GuideWorkflow) {
  const needed = requiredRuntimeIds(workflow);
  const missing = needed.filter(id => !runtimes.some(runtime => runtime.runtime_id === id && runtime.available));
  return {
    ready: missing.length === 0,
    status: missing.length ? `待配置：${missing.map(id => id === "blender" ? "Blender" : "DTS Explorer").join("、")}` : "所选工作路径的运行环境已可用",
  };
}
