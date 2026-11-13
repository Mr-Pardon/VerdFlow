export const guideSteps = [
  {
    id: "runtimes", route: "/settings", title: "选择工作方式，检查环境", section: "系统 / 设置",
    description: "在 DTS 场景中直接种植，使用 Explorer；从 FBX 模型提取绿地，使用 Blender。",
    hint: "只需准备所选路径的环境。已经可用的不必重复配置，也可以稍后再补齐。",
  },
  {
    id: "resources", route: "/resources", title: "准备第一套种植资源", section: "发现与创作 / 资源中心",
    description: "先准备本次要用的资产库和策略。打开卡片可以查看说明与版本，再按需导入。",
    hint: "不必全部导入，种植方案也是可选的。已有本地资源包，或暂时离线，都可以直接继续。",
  },
  {
    id: "assets", route: "/assets", title: "准备植被资产", section: "基础资源 / 资产库",
    description: "查看已导入的植被，或导入本地 .vfa 文件。资产库保存模型索引和缩略图，不会安装 DTS 中的模型本体。",
    hint: "在 DTS 中种植还需要相应的模型。需要配套 PAK 时，请查看该资产库的资源详情。",
    author: { name: "VFA Author", description: "供 Agent 整理植被资料、制作 .vfa 资产包的创作工具包。普通种植无需使用；导出的 ZIP 是创作工具，不是可直接导入的资产包。" },
  },
  {
    id: "strategies", route: "/strategies", title: "检查并信任策略", section: "基础资源 / 策略库",
    description: "查看已导入的策略，或导入本地 .vfs 文件。确认来源可靠后，再信任要使用的策略。",
    hint: "信任会允许策略代码在本机执行，并不代表它已通过安全审查。撤销信任的包不会被批量重新信任。",
    author: { name: "VFS Author", description: "供 Agent 设计空间算法、检查并打包 .vfs 策略的创作工具包。普通种植无需使用；导出的 ZIP 不是可直接导入的策略包。" },
  },
  {
    id: "models", route: "/model-services", title: "让 AI 辅助识别绿地", section: "运行 / 模型服务 · 可选",
    description: "配置模型服务后，可辅助从 FBX 模型中选出绿地材质。选择服务、填写密钥与模型，再测试连接。",
    hint: "这不是种植的前提：可以手动选择材质，DTS 实时种植也不依赖模型服务。",
  },
  {
    id: "workbench", route: "/dts", title: "选择适合的工作路径", section: "多源工作台 / DTS",
    description: "DTS 项目工作台适合需要保存、复查和继续编辑的任务，可从 FBX 或地理空间数据建立项目。实时选区种植则连接 Explorer，在场景中选面并直接种植。",
    hint: "现在先认识这两个入口，不必创建项目或启动实时种植。",
  },
  {
    id: "projects", route: "/projects", title: "集中管理项目", section: "项目管理器",
    description: "在工作台建立的项目会出现在这里。可以重新打开项目、继续种植、查看任务和导出结果。",
    hint: "实时种植会话不等于工作台项目，不会自动成为这里的项目卡片。",
  },
  {
    id: "plans", route: "/plans", title: "把好用的配置留下来", section: "快捷种植 / 种植方案 · 可选",
    description: "种植方案保存策略、植被搭配和种植参数。下次选用方案即可快速开始，也可以继续微调。这里统一管理、编辑和导入 .vfp 方案包。",
    hint: "有现成方案可以现在导入；没有也可以先手动配置，完成后再保存自己的方案。",
  },
] as const;

export type GuideStepId = (typeof guideSteps)[number]["id"];
export type GuideWorkflow = "live" | "fbx" | "both";
export const guideStorageKey = "verdflow.studio.first-use.v1";
export interface GuideProgress {
  version: 1;
  status: "paused" | "completed" | "dismissed";
  step: GuideStepId;
  workflow: GuideWorkflow;
  visited: GuideStepId[];
  skipped: GuideStepId[];
}
export const initialProgress = (): GuideProgress => ({
  version: 1, status: "paused", step: guideSteps[0].id, workflow: "live", visited: [], skipped: [],
});
const isStep = (value: unknown): value is GuideStepId => guideSteps.some(step => step.id === value);
export function readProgress(raw: string | null): GuideProgress | null {
  try {
    const value = JSON.parse(raw ?? "null");
    if (!value || value.version !== 1 || !isStep(value.step)
      || !["paused", "completed", "dismissed"].includes(value.status)
      || !["live", "fbx", "both"].includes(value.workflow)) return null;
    return { version: 1, status: value.status, step: value.step, workflow: value.workflow,
      visited: [...new Set<GuideStepId>((Array.isArray(value.visited) ? value.visited : []).filter(isStep))],
      skipped: [...new Set<GuideStepId>((Array.isArray(value.skipped) ? value.skipped : []).filter(isStep))] };
  } catch { return null; }
}
