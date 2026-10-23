import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import type { Ref } from "vue";

import { LocalApiClient, LocalApiError } from "../../api";
import type {
  MaterialClassification,
  MaterialClassificationCandidate,
  MaterialFactsView,
  MaterialFlowInfo,
  ProjectBusinessSummary,
  ProjectTaskInfo,
  PlantRunExecutionInfo,
  PlantPreviewInputInfo,
  PlantingCompositionSummary,
  ProjectXmlOutputInfo,
  ScenePreparationInfo,
} from "../../api";
import { platformBridge } from "../../platform";
import { requireProjectsState } from "../projects/state";

const ACTIVE_EXECUTION_STATUSES = new Set([
  "queued",
  "running",
  "cancel_requested",
]);

export const useDtsWorkspace = (projectId: Ref<string>) => {
  const projectsState = requireProjectsState();
  const client = new LocalApiClient(platformBridge());
  const summary = shallowRef<ProjectBusinessSummary | null>(null);
  const preparation = shallowRef<ScenePreparationInfo | null>(null);
  const plantRun = shallowRef<PlantRunExecutionInfo | null>(null);
  const plantInput = shallowRef<PlantPreviewInputInfo | null>(null);
  const composition = shallowRef<PlantingCompositionSummary | null>(null);
  const xmlOutputs = ref<ProjectXmlOutputInfo[]>([]);
  const materialFlow = shallowRef<MaterialFlowInfo | null>(null);
  const materialFacts = shallowRef<MaterialFactsView | null>(null);
  const materialFactsWarning = ref<string | null>(null);
  const candidate = shallowRef<MaterialClassificationCandidate | null>(null);
  const tasks = ref<ProjectTaskInfo[]>([]);
  const classifications = ref<Record<string, MaterialClassification>>({});
  const touchedMaterials = new Set<string>();
  const loading = ref(true);
  const refreshing = ref(false);
  const confirming = ref(false);
  const plantCancellationRequestPending = ref(false);
  const acceptedPlantCancellationId = ref<string | null>(null);
  const cancellingPlantPreview = computed(() =>
    plantCancellationRequestPending.value || (
      acceptedPlantCancellationId.value !== null &&
      acceptedPlantCancellationId.value === plantRun.value?.execution_id &&
      ["queued", "running"].includes(plantRun.value.status)
    ),
  );
  const plantCancellationError = ref<string | null>(null);
  const errorMessage = ref<string | null>(null);
  const lastUpdatedAt = ref<string | null>(null);
  let refreshTimer: ReturnType<typeof setTimeout> | undefined;
  let materialRevisionKey: string | null = null;
  let loadedMaterialFlowId: string | null = null;
  let loadedPlantInputId: string | null = null;
  let disposed = false;
  let plantRunUpdateSequence = 0;

  const project = computed(() =>
    projectsState.projects.value.find(
      (item) => item.projectId === projectId.value,
    ),
  );
  const requiresMaterialConfirmation = computed(
    () =>
      preparation.value?.status === "awaiting_confirmation" ||
      materialFlow.value?.status === "awaiting_confirmation",
  );
  const activeTask = computed(() =>
    tasks.value.find((task) =>
      ["queued", "running", "cancelling"].includes(task.status),
    ),
  );
  const materialFlowActive = computed(() =>
    ACTIVE_EXECUTION_STATUSES.has(materialFlow.value?.status ?? ""),
  );

  const reconcilePlantCancellation = () => {
    const current = plantRun.value;
    if (
      current?.execution_id !== acceptedPlantCancellationId.value ||
      !["queued", "running"].includes(current?.status ?? "")
    ) acceptedPlantCancellationId.value = null;
    if (!current || !["queued", "running"].includes(current.status)) {
      plantCancellationError.value = null;
    }
  };

  const refresh = async (options: { refreshRegistry?: boolean } = {}) => {
    if (refreshing.value || disposed) {
      return;
    }
    refreshing.value = true;
    const refreshPlantRunSequence = plantRunUpdateSequence;
    errorMessage.value = null;
    try {
      if (options.refreshRegistry || !project.value) {
        await projectsState.refresh();
      }
      let current = project.value;
      if (!current) {
        throw new Error("没有找到该项目");
      }
      if (!current.sessionId && current.availability === "available") {
        current = (await projectsState.openProject(current)) ?? current;
      }
      if (!current.sessionId) {
        throw new Error("项目尚未打开，无法读取运行状态");
      }

      const outputRequest = client
        .projectXmlOutputs(current.sessionId)
        .then((page) => page.items)
        .catch((error) => {
          console.warn("Could not refresh retained XML outputs", error);
          return null;
        });
      const [nextSummary, taskPage, compositionEnvelope, nextXmlOutputs] = await Promise.all([
        client.projectSummary(current.sessionId),
        client.projectTasks(current.sessionId),
        client.plantingComposition(current.sessionId),
        outputRequest,
      ]);
      if (disposed || refreshPlantRunSequence !== plantRunUpdateSequence) return;
      summary.value = nextSummary;
      tasks.value = taskPage.items;
      composition.value = compositionEnvelope.composition;
      if (nextXmlOutputs !== null) {
        xmlOutputs.value = nextXmlOutputs;
      }

      if (nextSummary.plant_run_execution_id) {
        const executionId = nextSummary.plant_run_execution_id;
        const nextPlantRun = await client.plantPreview(current.sessionId, executionId);
        if (disposed || refreshPlantRunSequence !== plantRunUpdateSequence) return;
        if (plantRun.value?.execution_id !== executionId) {
          plantInput.value = null;
          loadedPlantInputId = null;
          plantCancellationError.value = null;
        }
        plantRun.value = nextPlantRun;
        reconcilePlantCancellation();
        loading.value = false;
        // Execution requests are immutable; polling only needs the changing status.
        if (loadedPlantInputId !== executionId) {
          const nextPlantInput = await client.plantPreviewInput(current.sessionId, executionId);
          if (disposed || refreshPlantRunSequence !== plantRunUpdateSequence) return;
          plantInput.value = nextPlantInput;
          loadedPlantInputId = executionId;
        }
      } else {
        plantRun.value = null;
        plantInput.value = null;
        loadedPlantInputId = null;
        reconcilePlantCancellation();
      }
      loading.value = false;
      if (nextSummary.scene_preparation_execution_id) {
        await loadPreparation(
          current.sessionId,
          nextSummary.scene_preparation_execution_id,
          nextSummary.material_flow_execution_id,
        );
      } else {
        clearPreparation();
      }
      if (disposed || refreshPlantRunSequence !== plantRunUpdateSequence) return;
      lastUpdatedAt.value = new Date().toISOString();
      scheduleRefresh();
    } catch (error) {
      if (disposed || refreshPlantRunSequence !== plantRunUpdateSequence) return;
      errorMessage.value = readableError(error);
      scheduleRefresh(true);
    } finally {
      refreshing.value = false;
      loading.value = false;
      if (!disposed && refreshPlantRunSequence !== plantRunUpdateSequence) {
        void refresh();
      }
    }
  };

  const acceptPlantPreview = (
    execution: PlantRunExecutionInfo,
    input: PlantPreviewInputInfo | null,
  ) => {
    if (disposed) return;
    // A refresh started before submission must not overwrite the accepted task.
    plantRunUpdateSequence += 1;
    if (plantRun.value?.execution_id !== execution.execution_id) {
      plantCancellationError.value = null;
      plantInput.value = null;
      loadedPlantInputId = null;
    }
    if (input) plantInput.value = input;
    plantRun.value = execution;
    reconcilePlantCancellation();
    scheduleRefresh();
    void refresh();
  };

  const cancelPlantPreview = async () => {
    const execution = plantRun.value;
    const sessionId = project.value?.sessionId;
    if (
      !sessionId || !execution || cancellingPlantPreview.value ||
      !["queued", "running"].includes(execution.status)
    ) return;
    plantCancellationRequestPending.value = true;
    plantCancellationError.value = null;
    try {
      const result = await client.cancelPlantRun(sessionId, execution.execution_id);
      if (disposed || plantRun.value?.execution_id !== execution.execution_id) return;
      if (result.accepted && ["queued", "running"].includes(plantRun.value.status)) {
        acceptedPlantCancellationId.value = execution.execution_id;
      }
      if (result.execution.revision >= plantRun.value.revision) {
        acceptPlantPreview(result.execution, null);
      }
    } catch (error) {
      if (
        !disposed && plantRun.value?.execution_id === execution.execution_id &&
        ["queued", "running"].includes(plantRun.value.status)
      ) plantCancellationError.value = readableError(error);
    } finally {
      plantCancellationRequestPending.value = false;
    }
  };

  const loadPreparation = async (
    sessionId: string,
    executionId: string,
    currentMaterialFlowId: string | null,
  ) => {
    const nextPreparation = await client.scenePreparation(sessionId, executionId);
    preparation.value = nextPreparation;
    materialFactsWarning.value = null;
    const materialFlowId =
      currentMaterialFlowId ?? nextPreparation.material_flow_execution_id;
    const nextFlow = materialFlowId
      ? await client.materialFlow(sessionId, materialFlowId)
      : null;
    materialFlow.value = nextFlow;
    if (loadedMaterialFlowId !== materialFlowId) {
      touchedMaterials.clear();
      loadedMaterialFlowId = materialFlowId;
    }

    if (!nextPreparation.scene_revision_id) {
      return;
    }
    let nextFacts: MaterialFactsView;
    try {
      nextFacts = await client.materialFacts(
        sessionId,
        nextPreparation.scene_revision_id,
      );
    } catch (error) {
      if (isMissingProjectResource(error)) {
        materialFacts.value = null;
        candidate.value = null;
        classifications.value = {};
        materialFactsWarning.value =
          "该项目的历史材质事实已缺失；区域、种植结果与已有输出仍可继续查看。";
        return;
      }
      throw error;
    }
    materialFacts.value = nextFacts;
    candidate.value = await loadMaterialCandidate(sessionId, nextFlow);
    let confirmed: Awaited<
      ReturnType<typeof client.currentMaterialSelection>
    > | null = null;
    try {
      confirmed = await client.currentMaterialSelection(sessionId);
    } catch (error) {
      if (!isMissingProjectResource(error)) throw error;
      materialFactsWarning.value =
        "该项目的历史材质确认记录已缺失；现有区域与种植结果不受影响。";
    }
    synchronizeClassifications(nextFacts, candidate.value, confirmed?.materials);
  };

  const loadMaterialCandidate = async (
    sessionId: string,
    flow: MaterialFlowInfo | null,
  ): Promise<MaterialClassificationCandidate | null> => {
    if (!flow?.analysis_job_id || flow.stage === "analysis") {
      return null;
    }
    try {
      return await client.materialCandidate(sessionId, flow.analysis_job_id);
    } catch (error) {
      if (
        error instanceof LocalApiError &&
        (error.status === 404 ||
          (error.status === 409 &&
            error.message === "Material analysis is not configured"))
      ) {
        return null;
      }
      throw error;
    }
  };

  const synchronizeClassifications = (
    facts: MaterialFactsView,
    nextCandidate: MaterialClassificationCandidate | null,
    confirmed: Array<{
      material_name: string;
      classification: MaterialClassification;
    }> | undefined,
  ) => {
    const confirmedValues = new Map(
      confirmed?.map((item) => [item.material_name, item.classification]) ?? [],
    );
    const suggestions = new Map(
      nextCandidate?.materials.map((item) => [
        item.material_name,
        item.classification,
      ]) ?? [],
    );
    const next: Record<string, MaterialClassification> = {};
    for (const material of facts.materials) {
      next[material.material_name] = touchedMaterials.has(material.material_name)
        ? classifications.value[material.material_name] ?? "not_green"
        : confirmedValues.get(material.material_name) ??
          suggestions.get(material.material_name) ??
          "not_green";
    }
    classifications.value = next;
  };

  const setClassification = (
    materialName: string,
    classification: MaterialClassification,
  ) => {
    touchedMaterials.add(materialName);
    classifications.value = {
      ...classifications.value,
      [materialName]: classification,
    };
  };

  const confirmMaterials = async () => {
    const current = project.value;
    const execution = preparation.value;
    const facts = materialFacts.value;
    if (!current?.sessionId || !execution || !facts || materialFlowActive.value) {
      return;
    }
    confirming.value = true;
    errorMessage.value = null;
    try {
      const materials = facts.materials.map((material) => ({
          material_name: material.material_name,
          classification:
            classifications.value[material.material_name] ?? "not_green",
        }));
      if (["succeeded", "failed", "cancelled"].includes(execution.status)) {
        materialRevisionKey ??= crypto.randomUUID();
        materialFlow.value = await client.reviseMaterials({
          sessionId: current.sessionId,
          materials,
          idempotencyKey: materialRevisionKey,
        });
        materialRevisionKey = null;
        plantRun.value = null;
        plantInput.value = null;
        loadedPlantInputId = null;
        reconcilePlantCancellation();
      } else {
        preparation.value = await client.confirmScenePreparation({
          sessionId: current.sessionId,
          executionId: execution.execution_id,
          materials,
        });
      }
      await refresh();
    } catch (error) {
      errorMessage.value = readableError(error);
    } finally {
      confirming.value = false;
    }
  };

  const clearPreparation = () => {
    preparation.value = null;
    materialFlow.value = null;
    materialFacts.value = null;
    materialFactsWarning.value = null;
    candidate.value = null;
    loadedMaterialFlowId = null;
    touchedMaterials.clear();
  };

  const shouldRefresh = () =>
    ACTIVE_EXECUTION_STATUSES.has(summary.value?.execution_status ?? "") ||
    ACTIVE_EXECUTION_STATUSES.has(preparation.value?.status ?? "") ||
    ACTIVE_EXECUTION_STATUSES.has(plantRun.value?.status ?? "");

  const scheduleRefresh = (afterFailure = false) => {
    if (refreshTimer) {
      clearTimeout(refreshTimer);
    }
    if (disposed || document.visibilityState === "hidden") {
      return;
    }
    if (!afterFailure && !shouldRefresh()) {
      return;
    }
    refreshTimer = setTimeout(
      () => void refresh(),
      afterFailure ? 5000 : 2500,
    );
  };

  const handleVisibilityChange = () => {
    if (document.visibilityState === "visible" && shouldRefresh()) {
      void refresh();
    } else if (refreshTimer) {
      clearTimeout(refreshTimer);
    }
  };

  onMounted(() => {
    document.addEventListener("visibilitychange", handleVisibilityChange);
    void refresh({ refreshRegistry: true });
  });

  onBeforeUnmount(() => {
    disposed = true;
    if (refreshTimer) {
      clearTimeout(refreshTimer);
    }
    document.removeEventListener("visibilitychange", handleVisibilityChange);
  });

  return {
    activeTask,
    acceptPlantPreview,
    cancelPlantPreview,
    cancellingPlantPreview,
    candidate,
    classifications,
    composition,
    confirming,
    confirmMaterials,
    errorMessage,
    lastUpdatedAt,
    loading,
    materialFacts,
    materialFactsWarning,
    materialFlow,
    materialFlowActive,
    preparation,
    plantRun,
    plantCancellationError,
    plantInput,
    project,
    refreshing,
    refresh,
    requiresMaterialConfirmation,
    setClassification,
    summary,
    tasks,
    xmlOutputs,
  };
};

const readableError = (error: unknown): string => {
  if (error instanceof LocalApiError) {
    return error.code === "connection_failed"
      ? "无法连接 VerdFlow 本地服务，请确认 SystemHost 已启动"
      : error.message;
  }
  return error instanceof Error ? error.message : "发生未知错误";
};

const isMissingProjectResource = (error: unknown) =>
  error instanceof LocalApiError &&
  error.status === 404 &&
  error.code === "resource_not_found";
