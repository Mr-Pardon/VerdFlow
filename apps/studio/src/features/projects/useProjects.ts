import { computed, ref, shallowRef } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  ProjectBusinessSummary,
  ProjectSession,
  RecentProject,
  SystemHealth,
} from "../../api";
import { platformBridge } from "../../platform";
import { projectCardModel } from "./models";
import type { ProjectCardModel, ProjectFilter } from "./models";

export type ConnectionState = "connecting" | "connected" | "unavailable";

export const useProjects = () => {
  const client = shallowRef<LocalApiClient | null>(null);
  const health = ref<SystemHealth | null>(null);
  const projects = ref<ProjectCardModel[]>([]);
  const loading = ref(true);
  const openingProjectId = ref<string | null>(null);
  const deletingProjectId = ref<string | null>(null);
  const errorMessage = ref<string | null>(null);
  const notice = ref<string | null>(null);
  const connectionState = ref<ConnectionState>("connecting");
  let refreshSequence = 0;

  const activeCount = computed(() => health.value?.active_project_count ?? 0);
  const canSelectProjectDirectory =
    platformBridge().capabilities.selectProjectDirectory;

  const connect = async () => {
    connectionState.value = "connecting";
    const bridge = platformBridge();
    const connection = await bridge.getLocalApiConnection();
    if (!connection.ok) {
      connectionState.value = "unavailable";
      errorMessage.value = connection.message;
      loading.value = false;
      return;
    }
    client.value = new LocalApiClient(bridge);
    await refresh();
  };

  const refresh = async () => {
    const api = client.value;
    if (api === null) {
      return connect();
    }
    const sequence = ++refreshSequence;
    loading.value = true;
    errorMessage.value = null;
    try {
      const [nextHealth, recent, active] = await Promise.all([
        api.health(),
        api.recentProjects(),
        api.activeProjects(),
      ]);
      const summaries = await summariesFor(api, recent.items, active.items);
      if (sequence !== refreshSequence) {
        return;
      }
      health.value = nextHealth;
      projects.value = recent.items.map((project) =>
        projectCardModel(
          project,
          project.active_session_id
            ? summaries.get(project.active_session_id) ?? null
            : null,
        ),
      );
      connectionState.value = "connected";
    } catch (error) {
      if (sequence !== refreshSequence) {
        return;
      }
      connectionState.value = "unavailable";
      errorMessage.value = readableError(error);
    } finally {
      if (sequence === refreshSequence) {
        loading.value = false;
      }
    }
  };

  const openProject = async (project: ProjectCardModel) => {
    if (project.sessionId || project.availability !== "available") {
      return project;
    }
    const api = client.value;
    if (api === null) {
      return null;
    }
    openingProjectId.value = project.registryId;
    errorMessage.value = null;
    try {
      const session = await api.openProject(project.projectPath, project.workspace);
      notice.value = `已打开 ${session.name}`;
      await refresh();
      return projects.value.find(
        (item) => item.registryId === project.registryId,
      ) ?? null;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      openingProjectId.value = null;
    }
  };

  const revealProject = async (project: ProjectCardModel) => {
    const result = await platformBridge().revealPath(project.projectPath);
    notice.value = result.ok ? "已在资源管理器中显示" : result.message;
  };

  const deleteProject = async (project: ProjectCardModel) => {
    const api = client.value;
    if (api === null) {
      errorMessage.value = "本地服务尚未连接";
      return false;
    }
    deletingProjectId.value = project.registryId;
    errorMessage.value = null;
    try {
      const result = await api.deleteProject(project.registryId);
      notice.value = result.files_removed
        ? `已删除 ${project.name}`
        : `已从列表移除 ${project.name}`;
      await refresh();
      return true;
    } catch (error) {
      errorMessage.value = readableError(error);
      return false;
    } finally {
      deletingProjectId.value = null;
    }
  };

  const chooseAndOpenProject = async () => {
    const selected = await platformBridge().selectProjectDirectory();
    if (!selected.ok) {
      notice.value = selected.message;
      return null;
    }
    const api = client.value;
    if (api === null) {
      errorMessage.value = "本地服务尚未连接";
      return null;
    }
    openingProjectId.value = "__selected_project__";
    try {
      const session = await api.openProject(selected.value, null);
      notice.value = `已打开 ${session.name}`;
      await refresh();
      return session;
    } catch (error) {
      errorMessage.value = readableError(error);
      return null;
    } finally {
      openingProjectId.value = null;
    }
  };

  const dismissNotice = () => {
    notice.value = null;
  };

  return {
    activeCount,
    canSelectProjectDirectory,
    chooseAndOpenProject,
    connectionState,
    dismissNotice,
    deleteProject,
    deletingProjectId,
    errorMessage,
    health,
    loading,
    notice,
    openingProjectId,
    openProject,
    projects,
    refresh,
    revealProject,
  };
};

const summariesFor = async (
  api: LocalApiClient,
  recent: RecentProject[],
  active: ProjectSession[],
): Promise<Map<string, ProjectBusinessSummary>> => {
  const activeIds = new Set(active.map((project) => project.session_id));
  const sessionIds = recent
    .map((project) => project.active_session_id)
    .filter((value): value is string => value !== null && activeIds.has(value));
  const results = await Promise.allSettled(
    sessionIds.map(async (sessionId) => ({
      sessionId,
      summary: await api.projectSummary(sessionId),
    })),
  );
  return new Map(
    results.flatMap((result) =>
      result.status === "fulfilled"
        ? [[result.value.sessionId, result.value.summary] as const]
        : [],
    ),
  );
};

const readableError = (error: unknown): string => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed"
      ? "无法连接 VerdFlow 本地服务，请确认 SystemHost 已启动"
      : error.message;
  }
  return error instanceof Error ? error.message : "发生未知错误";
};

export const projectFilters: ReadonlyArray<{
  key: ProjectFilter;
  label: string;
}> = [
  { key: "all", label: "全部" },
  { key: "running", label: "进行中" },
  { key: "pending", label: "待处理" },
  { key: "completed", label: "已完成" },
  { key: "failed", label: "失败" },
];
