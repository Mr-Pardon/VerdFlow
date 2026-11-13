import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import {
  LocalApiClient,
  LocalApiError,
  type ProjectSession,
} from "../../api";
import { platformBridge } from "../../platform";
import { requireProjectsState } from "../projects/state";

type CreationStage =
  | "idle"
  | "creating_project"
  | "submitting_intake";

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
  const imageryAvailable = ref(false);
  const imageryMode = ref(false);
  const stage = ref<CreationStage>("idle");
  const errorMessage = ref<string | null>(null);
  const createdSession = ref<ProjectSession | null>(null);
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
      (!imageryMode.value || (imageryAvailable.value && vectorPath.value !== elevationPath.value)) &&
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
      if (managementAvailable.value) {
        imageryAvailable.value = (await client.health()).imagery_available === true;
      }
    } catch (error) {
      errorMessage.value = readableError(error);
    }
  };

  const openDialog = (mode: "vector" | "imagery" = "vector") => {
    if (busy.value) return;
    errorMessage.value = null;
    imageryMode.value = mode === "imagery";
    vectorPath.value = "";
    elevationPath.value = "";
    projectName.value = "";
    resetProgress();
    idempotencyKey.value = `studio:region-source:${crypto.randomUUID()}`;
    dialogOpen.value = true;
  };

  const chooseVectorSource = async () => {
    errorMessage.value = null;
    const vector = await bridge.selectSourceFile(imageryMode.value ? ["tif", "tiff"] : ["shp", "geojson", "json"]);
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
      stage.value = "submitting_intake";
      await client.submitSourceIntake(sessionId, {
        workflow: imageryMode.value ? "imagery" : "vector",
        source_path: vectorPath.value, elevation_path: elevationPath.value,
        idempotency_key: idempotencyKey.value,
      });
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
    imageryAvailable,
    imageryMode,
    managementAvailable,
    openDialog,
    projectName,
    projectPath,
    stage,
    submit,
    vectorPath,
  };
};

const sourceName = (value: string): string =>
  value.replace(/\\/g, "/").split("/").pop() || "Source";

const sourceStem = (value: string): string =>
  sourceName(value).replace(/\.(shp|geojson|json|tif|tiff)$/i, "") || "Geospatial";

const suggestedProjectName = (value: string): string =>
  `${sourceStem(value)} 种植`;

const readableError = (error: unknown): string => {
  if (error instanceof LocalApiError) return error.message;
  return error instanceof Error ? error.message : "创建项目时发生未知错误";
};
