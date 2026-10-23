import type {
  ProjectBusinessState,
  ProjectBusinessSummary,
  RecentProject,
} from "../../api";

export type ProjectFilter =
  | "all"
  | "running"
  | "pending"
  | "completed"
  | "failed";

export interface ProjectCardModel {
  registryId: string;
  name: string;
  projectPath: string;
  projectId: string | null;
  sessionId: string | null;
  workspace: "dts" | "blender" | null;
  sourceLabel: string;
  availability: RecentProject["availability"];
  lastOpenedAt: string;
  filter: Exclude<ProjectFilter, "all">;
  stateLabel: string;
  stageLabel: string | null;
  tone: "neutral" | "info" | "success" | "warning" | "danger";
  actionLabel: string;
  errorMessage: string | null;
  summary: ProjectBusinessSummary | null;
}

const statePresentation: Record<
  ProjectBusinessState,
  Pick<ProjectCardModel, "filter" | "stateLabel" | "tone" | "actionLabel">
> = {
  new: {
    filter: "pending",
    stateLabel: "待处理",
    tone: "neutral",
    actionLabel: "继续处理",
  },
  running: {
    filter: "running",
    stateLabel: "进行中",
    tone: "info",
    actionLabel: "查看进度",
  },
  awaiting_material_confirmation: {
    filter: "pending",
    stateLabel: "等待确认",
    tone: "warning",
    actionLabel: "继续处理",
  },
  ready_to_plant: {
    filter: "pending",
    stateLabel: "可种植",
    tone: "success",
    actionLabel: "继续种植",
  },
  awaiting_point_review: {
    filter: "pending",
    stateLabel: "待点位确认",
    tone: "warning",
    actionLabel: "查看点位",
  },
  completed: {
    filter: "completed",
    stateLabel: "已完成",
    tone: "success",
    actionLabel: "查看结果",
  },
  failed: {
    filter: "failed",
    stateLabel: "失败",
    tone: "danger",
    actionLabel: "查看问题",
  },
  cancelled: {
    filter: "failed",
    stateLabel: "已取消",
    tone: "neutral",
    actionLabel: "查看状态",
  },
};

const stageLabels: Record<string, string> = {
  inspection: "模型检查",
  material_flow: "绿地识别",
  analysis: "材质分析",
  confirmation: "等待确认",
  extraction: "绿地提取",
  generation: "生成点位",
  assignment: "分配植被",
  elevation: "高程采样",
  export: "生成 XML",
  review: "点位确认",
  completed: "已完成",
};

export const projectCardModel = (
  project: RecentProject,
  summary: ProjectBusinessSummary | null,
): ProjectCardModel => {
  if (project.availability !== "available") {
    return {
      registryId: project.registry_id,
      name: project.name,
      projectPath: project.project_path,
      projectId: project.project_id,
      sessionId: project.active_session_id,
      workspace: project.last_workspace,
      sourceLabel: "项目",
      availability: project.availability,
      lastOpenedAt: project.last_opened_at,
      filter: "failed",
      stateLabel: project.availability === "missing" ? "路径不存在" : "不可用",
      stageLabel: null,
      tone: "danger",
      actionLabel: "查看问题",
      errorMessage: project.last_error,
      summary,
    };
  }

  if (summary === null) {
    return {
      registryId: project.registry_id,
      name: project.name,
      projectPath: project.project_path,
      projectId: project.project_id,
      sessionId: project.active_session_id,
      workspace: project.last_workspace,
      sourceLabel: "项目",
      availability: project.availability,
      lastOpenedAt: project.last_opened_at,
      filter: "pending",
      stateLabel: project.active_session_id ? "状态读取中" : "可打开",
      stageLabel: null,
      tone: "neutral",
      actionLabel: project.active_session_id ? "查看状态" : "打开项目",
      errorMessage: project.last_error,
      summary,
    };
  }

  const presentation = statePresentation[summary.state];
  return {
    registryId: project.registry_id,
    name: project.name,
    projectPath: project.project_path,
    projectId: project.project_id,
    sessionId: project.active_session_id,
    workspace: project.last_workspace,
    sourceLabel:
      summary.scene_importer_id === "verdflow.geospatial"
        ? "SHP / GeoJSON"
        : summary.scene_importer_id === "verdflow-blender-worker"
          ? "FBX"
          : "Source",
    availability: project.availability,
    lastOpenedAt: project.last_opened_at,
    ...presentation,
    stageLabel: summary.execution_stage
      ? stageLabels[summary.execution_stage] || summary.execution_stage
      : null,
    errorMessage: summary.error_message || project.last_error,
    summary,
  };
};
