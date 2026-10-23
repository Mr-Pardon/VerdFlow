import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import {
  LocalApiClient,
  LocalApiError,
  type ModelProviderInfo,
  type ProjectSession,
  type SourceInfo,
} from "../../api";
import { platformBridge } from "../../platform";
import { requireProjectsState } from "../projects/state";

type CreationStage =
  | "idle"
  | "creating_project"
  | "importing_source"
  | "starting_inspection";

export const useDtsProjectCreation = () => {
  const bridge = platformBridge();
  const router = useRouter();
  const projectsState = requireProjectsState();
  const client = new LocalApiClient(bridge);

  const dialogOpen = ref(false);
  const sourcePath = ref("");
  const projectName = ref("");
  const projectPath = ref("");
  const selectionMode = ref<"ai_assisted" | "manual">("manual");
  const confirmationMode = ref<"automatic" | "manual">("manual");
  const providerId = ref("");
  const providers = ref<ModelProviderInfo[]>([]);
  const managementAvailable = ref(false);
  const loadingProviders = ref(false);
  const stage = ref<CreationStage>("idle");
  const errorMessage = ref<string | null>(null);
  const createdSession = ref<ProjectSession | null>(null);
  const importedSource = ref<SourceInfo | null>(null);
  const idempotencyKey = ref("");

  const configuredProviders = computed(() =>
    providers.value.filter(
      (provider) => provider.secret_configured && provider.default_model,
    ),
  );
  const busy = computed(() => stage.value !== "idle");
  const canSelectSource = computed(
    () =>
      bridge.capabilities.selectSourceFile &&
      bridge.capabilities.selectNewProjectDirectory,
  );
  const canSubmit = computed(
    () =>
      managementAvailable.value &&
      sourcePath.value.length > 0 &&
      projectName.value.trim().length > 0 &&
      projectPath.value.length > 0 &&
      (selectionMode.value === "manual" || providerId.value.length > 0),
  );

  const initialize = async () => {
    loadingProviders.value = true;
    try {
      const connection = await bridge.getLocalApiConnection();
      managementAvailable.value =
        connection.ok && connection.value.managementAvailable;
      const result = await client.modelProviders();
      providers.value = result.items;
      const firstConfigured = result.items.find(
        (provider) => provider.secret_configured && provider.default_model,
      );
      providerId.value = firstConfigured?.provider_id ?? "";
      selectionMode.value = firstConfigured ? "ai_assisted" : "manual";
    } catch (error) {
      errorMessage.value = readableError(error);
      selectionMode.value = "manual";
    } finally {
      loadingProviders.value = false;
    }
  };

  const chooseSource = async () => {
    errorMessage.value = null;
    const selected = await bridge.selectSourceFile(["fbx"]);
    if (!selected.ok) {
      if (selected.reason !== "cancelled") {
        errorMessage.value = selected.message;
      }
      return;
    }
    resetProgress();
    sourcePath.value = selected.value;
    projectName.value = sourceStem(selected.value);
    idempotencyKey.value = `studio:scene-preparation:${crypto.randomUUID()}`;
    dialogOpen.value = true;
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

  const setSelectionMode = (value: "ai_assisted" | "manual") => {
    if (value === "ai_assisted" && configuredProviders.value.length === 0) {
      return;
    }
    selectionMode.value = value;
    if (value === "manual") {
      confirmationMode.value = "manual";
    }
  };

  const submit = async () => {
    if (!canSubmit.value || busy.value) {
      return;
    }
    errorMessage.value = null;
    try {
      if (!createdSession.value) {
        stage.value = "creating_project";
        createdSession.value = await client.createProject({
          projectPath: projectPath.value,
          projectName: projectName.value.trim(),
          enableModelApiWorker: selectionMode.value === "ai_assisted",
        });
      }
      if (!importedSource.value) {
        stage.value = "importing_source";
        importedSource.value = await client.addFbxSource(
          createdSession.value.session_id,
          sourcePath.value,
        );
      }
      stage.value = "starting_inspection";
      await client.submitScenePreparation({
        sessionId: createdSession.value.session_id,
        sourceId: importedSource.value.source_id,
        selectionMode: selectionMode.value,
        confirmationMode: confirmationMode.value,
        analysisProviderId:
          selectionMode.value === "ai_assisted" ? providerId.value : null,
        idempotencyKey: idempotencyKey.value,
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
    if (!busy.value) {
      dialogOpen.value = false;
    }
  };

  const resetProgress = () => {
    projectPath.value = "";
    createdSession.value = null;
    importedSource.value = null;
    stage.value = "idle";
  };

  return {
    busy,
    canSelectSource,
    canSubmit,
    chooseProjectLocation,
    chooseSource,
    closeDialog,
    confirmationMode,
    configuredProviders,
    dialogOpen,
    errorMessage,
    initialize,
    loadingProviders,
    managementAvailable,
    projectName,
    projectPath,
    providerId,
    selectionMode,
    setSelectionMode,
    sourcePath,
    stage,
    submit,
  };
};

const sourceStem = (value: string): string => {
  const name = value.replace(/\\/g, "/").split("/").pop() || "VerdFlow Project";
  return name.replace(/\.fbx$/i, "") || "VerdFlow Project";
};

const readableError = (error: unknown): string => {
  if (error instanceof LocalApiError) {
    return error.message;
  }
  return error instanceof Error ? error.message : "创建项目时发生未知错误";
};
