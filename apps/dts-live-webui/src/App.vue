<script setup lang="ts">
import { computed, ref } from "vue";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  LoaderCircle,
  Play,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Sprout,
  Square,
  X,
} from "@lucide/vue";
import { useFloatingPanel } from "./features/useFloatingPanel";
import { useLiveSession } from "./features/useLiveSession";
import { useLiveConfiguration } from "./features/useLiveConfiguration";
import { useRegionDrafts } from "./features/useRegionDrafts";
import { useExamplePanel } from "./features/useExamplePanel";
import StrategyExampleSidebar from "./components/StrategyExampleSidebar.vue";
import StrategySelectionPanel from "./components/StrategySelectionPanel.vue";
import ProfileSelectionPanel from "./components/ProfileSelectionPanel.vue";
import ParameterSection from "./components/ParameterSection.vue";
import RegionSelectionPanel from "./components/RegionSelectionPanel.vue";
import MappingWorkspace from "./components/MappingWorkspace.vue";
import PlantingPlanSelection from "./components/PlantingPlanSelection.vue";
import { scrollPanelWheel } from "./shared/panelScroll";

const logoUrl = `${import.meta.env.BASE_URL}brand/logo-dark.png`;
const iconUrl = `${import.meta.env.BASE_URL}brand/icon-256.png`;

const errorMessage = ref<string | null>(null);
const entryMode = ref<"choose" | "preset" | "manual">("choose");
const adjustingPreset = ref(false);
const {
  panelCollapsed,
  panelElement,
  panelDragging,
  panelStyle,
  beginPanelDrag,
  togglePanel,
} = useFloatingPanel();
const session = useLiveSession(errorMessage, {
  initializeConfiguration: () => configuration.initializeOptions(),
  onRestored: (snapshot) =>
    regionController.restoreRegions(snapshot.selection_regions ?? []),
  onCompleted: () => regionController.completeRegions(),
  onRegionDrawn: (region) => regionController.addDrawnRegion(region),
  onDrawingFinished: () => {
    regionController.drawing.value = false;
  },
  onRegionClicked: (id) => {
    void regionController.revealRegion(id);
  },
});
const {
  sdk,
  loading,
  sdkReady,
  sdkStatus,
  submitting,
  cancelling,
  currentStage,
  operationActive,
  stageLabel,
  cancel,
  samplingFailures,
} = session;
const configuration = useLiveConfiguration({
  operationActive,
  loading,
  errorMessage,
});
const example = useExamplePanel(configuration, {
  panelCollapsed,
  operationActive,
  entryMode,
});
const {
  selectedStrategy,
  mappingReady,
  mappingOpen,
  loadingProfileParameters,
  parametersValid,
  refreshingConfiguration,
  refreshConfigurationOptions,
} = configuration;
const regionController = useRegionDrafts({
  sdk,
  sdkReady,
  operationActive,
  submitting,
  errorMessage,
  panelCollapsed,
  samplingFailures,
});
const {
  drawing,
  selectionLocked,
  enabledRegionCount,
  regions,
} = regionController;
const configurationReady = computed(
  () =>
    entryMode.value !== "choose" &&
    (entryMode.value !== "preset" ||
      configuration.selectedProfile.value !== null) &&
    sdkReady.value &&
    selectedStrategy.value !== null &&
    mappingReady.value &&
    !loadingProfileParameters.value &&
    parametersValid.value,
);
const canBegin = computed(
  () =>
    configurationReady.value &&
    !selectionLocked.value &&
    !drawing.value &&
    enabledRegionCount.value > 0,
);
const startSelection = async () => {
  const configurationInput = configuration.buildRunConfiguration();
  if (!configurationInput || !canBegin.value) return;
  await session.startSelection(
    configurationInput,
    regions.value
      .filter((region) => region.enabled)
      .map(({ region_id, name, coordinates, bbox }) => ({
        region_id,
        name,
        coordinates,
        bbox,
      })),
  );
};
</script>

<template>
  <div v-if="panelDragging" class="panel-drag-shield" aria-hidden="true"></div>
  <main
    :ref="
      (element) => {
        panelElement = element as HTMLElement | null;
      }
    "
    class="live-shell"
    :class="{ collapsed: panelCollapsed, dragging: panelDragging }"
    :style="panelStyle"
    @pointerdown.stop
    @pointermove.stop
    @pointerup.stop
    @mousedown.stop
    @mousemove.stop
    @mouseup.stop
    @click.stop
    @dblclick.stop
    @wheel="scrollPanelWheel"
    @contextmenu.stop
  >
    <header
      class="live-header"
      title="拖动调整面板位置"
      @pointerdown="beginPanelDrag"
    >
      <img v-if="panelCollapsed" class="brand-mark" :src="iconUrl" alt="VerdFlow" width="34" height="34" :draggable="false" />
      <div v-if="!panelCollapsed" class="header-copy">
        <div class="header-brand-line">
          <img class="live-brand" :src="logoUrl" alt="VerdFlow" width="126" height="25" :draggable="false" />
          <strong>快速种植</strong>
        </div>
        <span :class="{ connected: sdkReady }">{{ sdkStatus }}</span>
      </div>
      <button
        v-if="!panelCollapsed"
        class="icon-button configuration-refresh"
        title="同步 Studio 中的策略与种植方案"
        :disabled="refreshingConfiguration || operationActive || mappingOpen"
        @pointerdown.stop
        @click="refreshConfigurationOptions()"
      >
        <RotateCcw :size="16" :class="{ spin: refreshingConfiguration }" />
      </button>
      <button
        class="icon-button collapse-button"
        :title="panelCollapsed ? '展开面板' : '收起面板'"
        @pointerdown.stop
        @click="togglePanel"
      >
        <ChevronDown v-if="panelCollapsed" :size="18" />
        <ChevronUp v-else :size="18" />
      </button>
    </header>

    <div v-show="!panelCollapsed" class="live-content">
      <div v-if="errorMessage" class="message error-message">
        <AlertCircle :size="16" />
        <span>{{ errorMessage }}</span>
        <button title="关闭" @click="errorMessage = null">
          <X :size="15" />
        </button>
      </div>

      <div v-if="entryMode === 'choose'" class="live-method-choices">
        <button
          type="button"
          :disabled="loading || operationActive"
          @click="entryMode = 'preset'"
        >
          <Sprout :size="23" /><span
            ><strong>预设方案</strong><small>选用已保存的种植效果</small></span
          ><ArrowRight :size="16" />
        </button>
        <button
          type="button"
          :disabled="loading || operationActive"
          @click="
            adjustingPreset = false;
            entryMode = 'manual';
          "
        >
          <SlidersHorizontal :size="23" /><span
            ><strong>手动配置</strong><small>选择策略与植被搭配</small></span
          ><ArrowRight :size="16" />
        </button>
      </div>
      <PlantingPlanSelection
        v-if="entryMode === 'preset'"
        :controller="configuration"
        :disabled="selectionLocked"
        :anchor="panelElement"
        v-model:picker-open="example.planPickerOpen.value"
        :has-example="example.available.value"
        :example-open="example.open.value"
        @toggle-example="example.toggle"
        @back="entryMode = 'choose'"
        @adjust="
          adjustingPreset = true;
          entryMode = 'manual';
        "
      />
      <template v-if="entryMode === 'manual'">
        <button
          class="advanced-action live-method-back"
          type="button"
          :disabled="selectionLocked"
          @click="entryMode = 'choose'"
        >
          <ArrowLeft :size="15" />切换种植方式
        </button>
        <StrategySelectionPanel
          :controller="configuration"
          :loading="loading"
          :selection-locked="selectionLocked"
          :operation-active="operationActive"
          :initially-collapsed="adjustingPreset"
        />
      </template>

      <RegionSelectionPanel
        v-if="entryMode !== 'choose'"
        :controller="regionController"
        :sdk-ready="sdkReady"
      />

      <ProfileSelectionPanel
        v-if="entryMode === 'manual'"
        :controller="configuration"
        :selection-locked="selectionLocked"
      />

      <ParameterSection
        v-if="entryMode === 'manual'"
        :controller="configuration"
        :selection-locked="selectionLocked"
        :has-example="example.available.value"
        :example-open="example.open.value"
        @toggle-example="example.toggle"
      />
    </div>

    <section v-show="!panelCollapsed" class="operation-section">
      <div v-if="stageLabel" class="operation-status">
        <LoaderCircle v-if="operationActive" class="spin" :size="17" />
        <Check v-else-if="currentStage === 'completed'" :size="17" />
        <Square v-else :size="14" />
        <span>{{ stageLabel }}</span>
      </div>
      <button
        v-if="operationActive"
        class="secondary-button danger"
        :disabled="cancelling"
        @click="cancel"
      >
        <X :size="16" /> {{ cancelling ? "正在取消" : "取消本次种植" }}
      </button>
      <div v-else-if="entryMode !== 'choose'" class="operation-actions single">
        <button
          v-if="entryMode === 'manual'"
          class="secondary-button"
          :disabled="
            !mappingReady ||
            !parametersValid ||
            loadingProfileParameters ||
            configuration.savingProfile.value ||
            configuration.preparingPlanSave.value
          "
          @click="configuration.beginPlanSave()"
        >
          <Save :size="16" />{{
            configuration.preparingPlanSave.value
              ? "正在读取方案"
              : "保存种植方案"
          }}
        </button>
        <button
          class="primary-button"
          :disabled="!canBegin"
          @click="startSelection"
        >
          <Play :size="17" fill="currentColor" />
          {{ submitting ? "正在启动" : "开始种植"
          }}<span v-if="enabledRegionCount">
            · {{ enabledRegionCount }} 个面</span
          >
        </button>
      </div>
    </section>
  </main>

  <MappingWorkspace :controller="configuration" />
  <StrategyExampleSidebar
    v-if="example.open.value"
    :controller="example"
    :configuration="configuration"
    :anchor="panelElement"
  />
</template>
