import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import {
  LocalApiClient,
  LocalApiError,
  type ProjectSession,
  type ProjectTaskInfo,
  type SourceInfo,
  type SourceKind,
} from "../../api";
import { platformBridge } from "../../platform";
import { requireProjectsState } from "../projects/state";

type CreationStage =
  | "idle"
  | "creating_project"
  | "importing_vector"
  | "importing_elevation"
  | "assembling_sources"
  | "preparing_regions";

const TERMINAL_FAILURES = new Set(["failed", "cancelled", "interrupted"]);

export const useDtsGeospatialProjectCreation = () => {
  const bridge = platformBridge();
  const router = useRouter();
  const projectsState = requireProjectsState();
  const client = new LocalApiClient(bridge);

  const dialogOpen = ref(false);
  const vectorPath = ref("");
  const elevationPath = ref("");
  const projectName = ref("");
  const projectPath = ref("");
  const managementAvailable = ref(false);
  const stage = ref<CreationStage>("idle");
  const errorMessage = ref<string | null>(null);
  const createdSession = ref<ProjectSession | null>(null);
  const vectorSource = ref<SourceInfo | null>(null);
  const elevationSource = ref<SourceInfo | null>(null);
  const assemblySource = ref<SourceInfo | null>(null);
  const regionJobId = ref<string | null>(null);
  const idempotencyKey = ref("");

  const busy = computed(() => stage.value !== "idle");
  const canSelectSources = computed(
    () =>
      bridge.capabilities.selectSourceFile &&
      bridge.capabilities.selectNewProjectDirectory,
  );
  const canSubmit = computed(
    () =>
      managementAvailable.value &&
      vectorPath.value.length > 0 &&
      elevationPath.value.length > 0 &&
      projectName.value.trim().length > 0 &&
      projectPath.value.length > 0,
  );

  const initialize = async () => {
    try {
      const connection = await bridge.getLocalApiConnection();
      managementAvailable.value =
        connection.ok && connection.value.managementAvailable;
    } catch (error) {
      errorMessage.value = readableError(error);
    }
  };

  const openDialog = () => {
    if (busy.value) return;
    errorMessage.value = null;
    vectorPath.value = "";
    elevationPath.value = "";
    projectName.value = "";
    resetProgress();
    idempotencyKey.value = `studio:region-source:${crypto.randomUUID()}`;
    dialogOpen.value = true;
  };

  const chooseVectorSource = async () => {
    errorMessage.value = null;
    const vector = await bridge.selectSourceFile(["shp", "geojson", "json"]);
    if (!vector.ok) {
      if (vector.reason !== "cancelled") errorMessage.value = vector.message;
      return;
    }
    const previousSuggestion = vectorPath.value
      ? suggestedProjectName(vectorPath.value)
      : "";
    const shouldSuggestName =
      !projectName.value.trim() || projectName.value === previousSuggestion;
    if (vector.value !== vectorPath.value) {
      vectorSource.value = null;
      assemblySource.value = null;
      regionJobId.value = null;
      idempotencyKey.value = `studio:region-source:${crypto.randomUUID()}`;
    }
    vectorPath.value = vector.value;
    if (shouldSuggestName) {
      projectName.value = suggestedProjectName(vector.value);
    }
  };

  const chooseElevationSource = async () => {
    errorMessage.value = null;
    const elevation = await bridge.selectSourceFile(["tif", "tiff"]);
    if (!elevation.ok) {
      if (elevation.reason !== "cancelled") {
        errorMessage.value = elevation.message;
      }
      return;
    }
    if (elevation.value !== elevationPath.value) {
      elevationSource.value = null;
      assemblySource.value = null;
      regionJobId.value = null;
      idempotencyKey.value = `studio:region-source:${crypto.randomUUID()}`;
    }
    elevationPath.value = elevation.value;
  };

  const chooseProjectLocation = async () => {
    errorMessage.value = null;
    const selected = await bridge.selectNewProjectDirectory({
      suggestedName: projectName.value,
    });
    if (selected.ok) {
      projectPath.value = selected.value;
    } else if (selected.reason !== "cancelled") {
      errorMessage.value = selected.message;
    }
  };

  const submit = async () => {
    if (!canSubmit.value || busy.value) return;
    errorMessage.value = null;
    try {
      if (!createdSession.value) {
        stage.value = "creating_project";
        createdSession.value = await client.createProject({
          projectPath: projectPath.value,
          projectName: projectName.value.trim(),
          enableModelApiWorker: false,
          enableBlenderWorker: false,
          enableGeospatialWorker: true,
        });
      }
      const sessionId = createdSession.value.session_id;
      if (!vectorSource.value) {
        stage.value = "importing_vector";
        vectorSource.value = await client.addSource(
          sessionId,
          vectorPath.value,
          vectorKind(vectorPath.value),
        );
      }
      if (!elevationSource.value) {
        stage.value = "importing_elevation";
        elevationSource.value = await client.addSource(
          sessionId,
          elevationPath.value,
          "geotiff",
        );
      }
      if (!assemblySource.value) {
        stage.value = "assembling_sources";
        assemblySource.value = await client.createIdentitySourceAssembly({
          sessionId,
          originalName: `${sourceName(vectorPath.value)} + ${sourceName(elevationPath.value)}`,
          sourceIds: [
            vectorSource.value.source_id,
            elevationSource.value.source_id,
          ],
        });
      }
      stage.value = "preparing_regions";
      if (!regionJobId.value) {
        const job = await client.submitRegionSource({
          sessionId,
          sourceId: assemblySource.value.source_id,
          idempotencyKey: idempotencyKey.value,
        });
        regionJobId.value = job.job_id;
      }
      await waitForJob(client, sessionId, regionJobId.value);
      await client.finalizeRegionSource(sessionId, regionJobId.value);
      const projectId = createdSession.value.project_id;
      dialogOpen.value = false;
      await projectsState.refresh();
      await router.push(`/projects/${encodeURIComponent(projectId)}/dts`);
    } catch (error) {
      errorMessage.value = createdSession.value
        ? `项目已保留。${readableError(error)}`
        : readableError(error);
    } finally {
      stage.value = "idle";
    }
  };

  const closeDialog = () => {
    if (!busy.value) dialogOpen.value = false;
  };

  const resetProgress = () => {
    projectPath.value = "";
    createdSession.value = null;
    vectorSource.value = null;
    elevationSource.value = null;
    assemblySource.value = null;
    regionJobId.value = null;
    stage.value = "idle";
  };

  return {
    busy,
    canSelectSources,
    canSubmit,
    chooseElevationSource,
    chooseProjectLocation,
    chooseVectorSource,
    closeDialog,
    dialogOpen,
    elevationPath,
    errorMessage,
    initialize,
    managementAvailable,
    openDialog,
    projectName,
    projectPath,
    stage,
    submit,
    vectorPath,
  };
};

const waitForJob = async (
  client: LocalApiClient,
  sessionId: string,
  jobId: string,
): Promise<ProjectTaskInfo> => {
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    const page = await client.projectTasks(sessionId, 100);
    const task = page.items.find((item) => item.job_id === jobId);
    if (task?.status === "succeeded") return task;
    if (task && TERMINAL_FAILURES.has(task.status)) {
      throw new Error(task.error_message || "区域数据准备失败");
    }
    await delay(500);
  }
  throw new Error("区域数据准备超时，项目已保留，可稍后重新打开检查任务状态");
};

const delay = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

const vectorKind = (path: string): SourceKind =>
  path.toLocaleLowerCase().endsWith(".shp") ? "shapefile" : "geojson";

const sourceName = (value: string): string =>
  value.replace(/\\/g, "/").split("/").pop() || "Source";

const sourceStem = (value: string): string =>
  sourceName(value).replace(/\.(shp|geojson|json)$/i, "") || "Geospatial";

const suggestedProjectName = (value: string): string =>
  `${sourceStem(value)} 种植`;

const readableError = (error: unknown): string => {
  if (error instanceof LocalApiError) return error.message;
  return error instanceof Error ? error.message : "创建项目时发生未知错误";
};
