<script setup lang="ts">
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleCheck,
  Layers,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  SlidersHorizontal,
  Sprout,
  Trees,
  X,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";

import type {
  PlantPreviewInputInfo,
  PlantRunExecutionInfo,
  StrategyLibrarySummary,
} from "../../api";
import RoleMappingEditor from "../../components/RoleMappingEditor.vue";
import StrategyEffectWorkspace from "../../components/StrategyEffectWorkspace.vue";
import { confirmAction } from "../../components/confirmDialog";
import LibraryThumbnail from "../library/LibraryThumbnail.vue";
import PlantingPlanCard from "../library/PlantingPlanCard.vue";
import { usePlantingPlans } from "../library/usePlantingPlans";
import { validCandidates } from "../shared/roleMapping";
import { strategyExampleAppearance } from "../shared/strategyExampleAppearance";
import type { StrategyExampleCache } from "@verdflow/strategy-preview-ui";
import {
  catalogKey,
  usePlantingConfiguration,
} from "./usePlantingConfiguration";
import { usePlantingChoicePreviews } from "./usePlantingChoicePreviews";

const props = defineProps<{
  sessionId: string;
  sceneRevisionId: string;
  regionSetId: string;
  selectedRegionIds: string[];
  initialInput: PlantPreviewInputInfo | null;
  currentExecution: PlantRunExecutionInfo | null;
  replacingBatchOrdinal?: number | null;
}>();
const emit = defineEmits<{
  back: [];
  previewSubmitted: [
    execution: PlantRunExecutionInfo,
    input: PlantPreviewInputInfo | null,
  ];
  submittingChanged: [submitting: boolean];
}>();
const planting = usePlantingConfiguration({
  sessionId: props.sessionId,
  sceneRevisionId: props.sceneRevisionId,
  regionSetId: props.regionSetId,
  selectedRegionIds: () => props.selectedRegionIds,
  initialInput: props.initialInput,
  currentExecution: () => props.currentExecution,
  onPreviewSubmitted: (execution, input) =>
    emit("previewSubmitted", execution, input),
});
const previews = usePlantingChoicePreviews();
const exampleCache: StrategyExampleCache = { signature: "", result: null };
const exampleAppearance = computed(() =>
  strategyExampleAppearance(
    planting.roleMappings.value,
    planting.profileDetail.value?.assignments ?? {},
    planting.assets.value,
  ),
);
const plans = usePlantingPlans();
const entryMode = ref<"choose" | "preset" | "manual">("choose");
const editingStep = ref<"strategy" | "mapping" | "parameters">("strategy");
const mappingMode = ref<"choose" | "custom">("choose");
const activeRoleKey = ref("");
const saveDialogOpen = ref(false);
const saveDisplayName = ref("");
const saveAsNew = ref(true);
const generating = planting.generating;
const busy = computed(
  () =>
    generating.value ||
    planting.loadingDetails.value ||
    Boolean(planting.trustingStrategyHash.value) ||
    planting.savingProfile.value ||
    planting.syncingProfileAssets.value,
);

interface StrategyGroup {
  strategyId: string;
  displayName: string;
  versions: StrategyLibrarySummary[];
}
const strategyGroups = computed<StrategyGroup[]>(() => {
  const groups = new Map<string, StrategyLibrarySummary[]>();
  for (const strategy of planting.strategies.value) {
    const versions = groups.get(strategy.strategy_id) ?? [];
    versions.push(strategy);
    groups.set(strategy.strategy_id, versions);
  }
  return [...groups.entries()]
    .map(([strategyId, versions]) => {
      versions.sort((left, right) =>
        right.strategy_version.localeCompare(left.strategy_version, undefined, {
          numeric: true,
          sensitivity: "base",
        }),
      );
      const selected = versions.find(
        (item) => item.package_hash === planting.selectedStrategyHash.value,
      );
      return {
        strategyId,
        displayName: selected?.display_name ?? versions[0].display_name,
        versions,
      };
    })
    .sort((left, right) =>
      left.displayName.localeCompare(right.displayName, "zh-CN"),
    );
});
const displayedStrategyVersion = (group: StrategyGroup) =>
  group.versions.find(
    (item) => item.package_hash === planting.selectedStrategyHash.value,
  ) ?? group.versions[0];
const strategyGroupActive = (group: StrategyGroup) =>
  group.versions.some(
    (item) => item.package_hash === planting.selectedStrategyHash.value,
  );
const selectedStrategyGroup = computed(
  () => strategyGroups.value.find(strategyGroupActive) ?? null,
);
const roles = computed(() => planting.strategyDetail.value?.roles ?? []);
const missingRoles = computed(() =>
  roles.value.filter((role) =>
    planting.missingRequiredRoles.value.includes(role.role_key),
  ),
);
const configuredRoleCount = computed(
  () =>
    roles.value.filter(
      (role) =>
        planting.roleEnabled.value[role.role_key] !== false &&
        validCandidates(planting.roleMappings.value[role.role_key]),
    ).length,
);
const selectedPlants = computed(() => {
  const assets = new Map(
    planting.assets.value.map((asset) => [asset.asset_id, asset]),
  );
  const plants = new Map<
    string,
    { assetId: string; name: string; resourceId: string | null }
  >();
  for (const role of roles.value) {
    if (planting.roleEnabled.value[role.role_key] === false) continue;
    for (const candidate of planting.roleMappings.value[role.role_key] ?? []) {
      const asset = candidate.asset ?? assets.get(candidate.assetId);
      plants.set(candidate.assetId, {
        assetId: candidate.assetId,
        name: asset?.display_name || candidate.displayNameSnapshot,
        resourceId: asset?.thumbnail_resource_id ?? null,
      });
    }
  }
  return [...plants.values()];
});
const steps = computed(() => [
  {
    key: "strategy" as const,
    title: "种植方式",
    summary: planting.selectedStrategy.value?.display_name || "待选择",
    ready: Boolean(planting.strategyDetail.value),
  },
  {
    key: "mapping" as const,
    title: "植被搭配",
    summary: planting.mappingReady.value
      ? planting.selectedProfile.value?.display_name || "本次自定义搭配"
      : "待选择",
    ready: planting.mappingReady.value,
  },
  {
    key: "parameters" as const,
    title: "效果与预览",
    summary: planting.customizedParameterCount.value
      ? `${planting.customizedParameterCount.value} 项已调整`
      : "使用默认效果",
    ready: planting.mappingReady.value && planting.parametersValid.value,
  },
]);
const generationStatus = computed(() => {
  if (entryMode.value === "choose" && !generating.value)
    return "请选择种植方式";
  if (
    entryMode.value === "preset" &&
    !planting.selectedProfile.value &&
    !generating.value
  )
    return "请选择种植方案";
  const execution = planting.execution.value;
  if (execution?.status === "cancel_requested") return "正在取消种植任务…";
  if (execution?.status === "failed")
    return execution.error_message || "点位生成失败";
  if (execution?.status === "cancelled")
    return "种植任务已取消，可调整设置后重新生成";
  if (generating.value)
    return execution?.stage === "assignment"
      ? "正在分配植被…"
      : "正在生成点位…";
  if (planting.loadingDetails.value) return "正在加载配置…";
  if (!props.selectedRegionIds.length) return "请先选择种植区域";
  if (editingStep.value === "strategy")
    return planting.selectedStrategy.value
      ? "种植方式已选定"
      : "请选择种植方式";
  if (missingRoles.value.length)
    return `还需配置：${missingRoles.value.map((role) => role.display_name).join("、")}`;
  if (!planting.mappingReady.value) return "请选择植被搭配，或自行选择植被";
  if (
    !planting.parametersValid.value ||
    (planting.randomSeed.value !== null &&
      !Number.isSafeInteger(planting.randomSeed.value))
  )
    return "请检查效果参数与随机种子";
  if (planting.parametersChangedSinceGeneration.value)
    return "设置已变化，生成后更新预览";
  return `${selectedPlants.value.length} 种植被 · ${props.selectedRegionIds.length} 个区域`;
});

watch(
  () =>
    strategyGroups.value
      .map((group) => displayedStrategyVersion(group).package_hash)
      .join("|"),
  () =>
    void previews.loadStrategies(
      strategyGroups.value.map(
        (group) => displayedStrategyVersion(group).package_hash,
      ),
    ),
);
watch(
  () =>
    planting.profiles.value
      .map((profile) => `${profile.profile_id}:${profile.generation}`)
      .join("|"),
  () =>
    void previews.loadProfiles(
      planting.profiles.value,
      planting.catalogs.value,
    ),
);
watch(
  planting.submitting,
  (submitting) => emit("submittingChanged", submitting),
  { flush: "sync" },
);

const focusRole = (key: string) => {
  mappingMode.value = "custom";
  editingStep.value = "mapping";
  activeRoleKey.value = key;
};
const focusFirstRole = () => {
  activeRoleKey.value =
    missingRoles.value[0]?.role_key || roles.value[0]?.role_key || "";
};
onMounted(async () => {
  await Promise.all([planting.initialize(), plans.refresh()]);
  if (props.initialInput || planting.generating.value)
    entryMode.value = "manual";
  editingStep.value = !planting.selectedStrategy.value
    ? "strategy"
    : planting.mappingReady.value
      ? "parameters"
      : "mapping";
  mappingMode.value =
    planting.selectedProfile.value && !planting.mappingDirty.value
      ? "choose"
      : Object.keys(planting.roleMappings.value).length
        ? "custom"
        : "choose";
  focusFirstRole();
});
const confirmDiscardMapping = async () =>
  !planting.mappingDirty.value ||
  (await confirmAction({
    title: "放弃本次搭配修改？",
    message: "切换后将丢失未保存的植被选择、分配和缩放设置。",
    confirmLabel: "放弃修改",
    tone: "danger",
  }));
const selectStrategy = async (packageHash: string) => {
  if (busy.value) return;
  const strategy = planting.strategies.value.find(
    (item) => item.package_hash === packageHash,
  );
  if (
    strategy?.executable &&
    planting.selectedStrategyHash.value === packageHash
  )
    return;
  if (!(await confirmDiscardMapping())) return;
  if (strategy && !strategy.executable) {
    const confirmed = await confirmAction({
      title: `信任策略“${strategy.display_name}”？`,
      message: "信任后，该策略的生成器可在本机项目中执行。",
      detail: `仅信任当前包哈希：${strategy.package_hash}`,
      confirmLabel: "信任并使用",
    });
    if (!confirmed || !(await planting.trustStrategy(packageHash))) return;
  }
  await planting.chooseStrategy(packageHash);
  mappingMode.value = "choose";
  focusFirstRole();
};

const selectProfile = async (id: string) => {
  if (busy.value) return;
  if (planting.selectedMappingKey.value !== id) {
    if (!(await confirmDiscardMapping())) return;
    await planting.chooseMappingSource(id);
  }
  mappingMode.value = "choose";
  if (!planting.mappingReady.value && planting.profileDetail.value)
    focusRole(
      missingRoles.value[0]?.role_key || roles.value[0]?.role_key || "",
    );
};
const startCustomMapping = async () => {
  if (busy.value) return;
  if (planting.selectedProfile.value) {
    if (!(await confirmDiscardMapping())) return;
    await planting.chooseMappingSource("");
  }
  if (!planting.selectedCatalog.value && planting.catalogs.value.length === 1) {
    await planting.chooseCatalog(catalogKey(planting.catalogs.value[0]));
  }
  mappingMode.value = "custom";
  focusFirstRole();
};
const selectPlan = async (
  plan: import("../../api").RoleBindingProfileSummary,
) => {
  if (busy.value) return;
  const strategy = planting.strategies.value.find(
    (item) =>
      plan.resolved_strategy_hash
        ? item.package_hash === plan.resolved_strategy_hash
        : item.strategy_id === plan.strategy_id &&
          item.strategy_version === plan.strategy_version,
  );
  if (!strategy || plan.strategy_resolution_issue) {
    planting.errorMessage.value = plan.strategy_resolution_issue || "请先安装此方案对应的策略版本";
    return;
  }
  await selectStrategy(strategy.package_hash);
  if (
    planting.selectedStrategyHash.value !== strategy.package_hash ||
    !planting.selectedStrategy.value?.executable
  )
    return;
  await planting.chooseProfile(plan.profile_id);
};
const adjustPlan = () => {
  entryMode.value = "manual";
  editingStep.value = "parameters";
  mappingMode.value = "custom";
  focusFirstRole();
};
const editMapping = () => {
  editingStep.value = planting.selectedStrategy.value ? "mapping" : "strategy";
};
const editParameters = () => {
  if (!planting.mappingReady.value) return;
  editingStep.value = "parameters";
};
const navigateStep = (step: typeof editingStep.value) => {
  if (busy.value) return;
  if (step === "mapping" && !planting.strategyDetail.value) return;
  if (step === "parameters" && !planting.mappingReady.value) return;
  editingStep.value = step;
};
const rerollSeed = () => {
  planting.rerollRandomSeed();
};
const openSaveDialog = () => {
  saveDisplayName.value =
    planting.selectedProfile.value?.display_name ||
    `${planting.selectedStrategy.value?.display_name ?? "种植方式"} · 自定义方案`;
  saveAsNew.value = !planting.selectedProfile.value;
  saveDialogOpen.value = true;
};
const saveProfile = async () => {
  if (await planting.saveProfile(saveDisplayName.value, saveAsNew.value)) {
    saveDialogOpen.value = false;
    await plans.refresh();
  }
};
const submitPreview = async () => {
  if (busy.value) return;
  if (
    props.currentExecution &&
    !(await confirmAction({
      title: "重新生成点位？",
      message: "将替换当前预览点位。已保存批次与历史 XML 不受影响。",
      confirmLabel: "重新生成",
      tone: "danger",
    }))
  )
    return;
  await planting.submitPreview();
};
</script>

<template>
  <section class="planting-stage planting-guide">
    <header class="planting-header">
      <div>
        <span>植被种植</span>
        <h2>
          {{
            entryMode === "choose"
              ? "选择种植方式"
              : entryMode === "preset"
                ? "预设方案"
                : "配置本次种植"
          }}
        </h2>
      </div>
      <div class="planting-header-tools">
        <div
          v-if="entryMode !== 'choose'"
          class="planting-mode-switch"
          role="radiogroup"
          aria-label="种植方式"
        >
          <button
            type="button"
            role="radio"
            :aria-checked="entryMode === 'preset'"
            :class="{ active: entryMode === 'preset' }"
            :disabled="busy"
            @click="entryMode = 'preset'"
          >
            <Sprout :size="17" /><span>预设方案</span>
          </button>
          <button
            type="button"
            role="radio"
            :aria-checked="entryMode === 'manual'"
            :class="{ active: entryMode === 'manual' }"
            :disabled="busy"
            @click="entryMode = 'manual'"
          >
            <SlidersHorizontal :size="17" /><span>手动配置</span>
          </button>
        </div>
        <div class="guide-region-count">
          <Layers :size="16" /><strong>{{ selectedRegionIds.length }}</strong
          ><span>个选区</span>
        </div>
      </div>
    </header>
    <div
      v-if="
        replacingBatchOrdinal !== null && replacingBatchOrdinal !== undefined
      "
      class="planting-replacement-note"
    >
      <strong>正在重新种植第 {{ replacingBatchOrdinal + 1 }} 批</strong
      ><span>旧批次保留到新点位确认提交。</span>
    </div>
    <div v-if="planting.errorMessage.value" class="planting-error" role="alert">
      {{ planting.errorMessage.value }}
    </div>
    <div v-if="planting.loading.value" class="planting-loading">
      <LoaderCircle :size="22" class="spinning" />正在读取种植配置
    </div>
    <div v-else-if="entryMode === 'choose'" class="planting-method-grid">
      <button
        class="planting-method-card"
        type="button"
        :disabled="busy"
        @click="entryMode = 'preset'"
      >
        <Sprout /><strong>预设方案</strong
        ><span>选用已保存的植被与种植效果</span><ArrowRight />
      </button>
      <button
        class="planting-method-card"
        type="button"
        :disabled="busy"
        @click="entryMode = 'manual'"
      >
        <SlidersHorizontal /><strong>手动配置</strong
        ><span>自行选择策略、植被与效果参数</span><ArrowRight />
      </button>
    </div>
    <div v-else-if="entryMode === 'preset'" class="preset-planting-panel">
      <header class="preset-planting-heading">
        <h3>选择种植方案</h3>
        <input
          v-model="plans.query.value"
          class="search-control"
          type="search"
          placeholder="搜索方案或植被"
          aria-label="搜索种植方案"
        />
      </header>
      <p v-if="plans.error.value" class="planting-error">
        {{ plans.error.value }}
      </p>
      <div class="plan-card-grid">
        <PlantingPlanCard
          v-for="plan in plans.filtered.value"
          :key="plan.profile_id"
          :plan="plan"
          :strategy-name="plans.strategyFor(plan)?.display_name"
          selectable
          :selected="
            planting.selectedProfile.value?.profile_id === plan.profile_id
          "
          :disabled="busy"
          :unavailable="
            plan.strategy_resolution_issue || (!plans.strategyFor(plan)
              ? '对应策略尚未安装'
              : !plans.strategyFor(plan)?.executable
                ? '使用前需信任策略'
                : undefined)
          "
          @choose="selectPlan(plan)"
        />
      </div>
      <p
        v-if="!plans.loading.value && !plans.filtered.value.length"
        class="planting-empty"
      >
        {{
          plans.plans.value.length
            ? "没有匹配的方案"
            : "暂无种植方案，可使用手动配置。"
        }}
      </p>
      <div
        v-if="planting.selectedProfile.value && planting.mappingReady.value"
        class="preset-planting-selection"
      >
        <div>
          <strong>{{ planting.selectedProfile.value.display_name }}</strong
          ><span>{{ planting.parameterSummary.value }}</span>
        </div>
        <button
          class="secondary-button"
          type="button"
          :disabled="busy"
          @click="adjustPlan"
        >
          <Pencil :size="15" />调整方案
        </button>
      </div>
    </div>
    <div v-else class="planting-progressive">
      <nav class="guide-steps" aria-label="本次种植步骤">
        <button
          v-for="(step, index) in steps"
          :key="step.key"
          type="button"
          :class="{ active: editingStep === step.key, complete: step.ready }"
          :aria-current="editingStep === step.key ? 'step' : undefined"
          :disabled="
            busy ||
            (index === 1 && !planting.strategyDetail.value) ||
            (index === 2 && !planting.mappingReady.value)
          "
          @click="navigateStep(step.key)"
        >
          <span class="guide-step-number"
            ><Check
              v-if="step.ready && editingStep !== step.key"
              :size="15"
            /><template v-else>{{ index + 1 }}</template></span
          >
          <span
            ><small>{{ step.title }}</small
            ><strong>{{ step.summary }}</strong></span
          >
          <Pencil v-if="step.ready && editingStep !== step.key" :size="14" />
        </button>
      </nav>
      <section class="planting-column planting-editor-panel guide-content">
        <div class="guide-main">
          <template v-if="editingStep === 'strategy'">
            <header class="guide-section-heading">
              <div>
                <span>01 / 种植方式</span>
                <h3>选择空间构图</h3>
              </div>
            </header>
            <div
              class="guide-strategy-grid"
              role="radiogroup"
              aria-label="种植方式"
            >
              <button
                v-for="group in strategyGroups"
                :key="group.strategyId"
                class="guide-strategy-card"
                type="button"
                role="radio"
                :aria-checked="strategyGroupActive(group)"
                :class="{ selected: strategyGroupActive(group) }"
                :disabled="busy"
                @click="
                  selectStrategy(displayedStrategyVersion(group).package_hash)
                "
              >
                <span class="guide-choice-heading"
                  ><span class="guide-choice-icon"><Sprout :size="21" /></span
                  ><strong>{{ group.displayName }}</strong
                  ><CircleCheck
                    v-if="strategyGroupActive(group)"
                    :size="19" /><span v-else class="guide-radio"
                /></span>
                <span class="guide-strategy-description">{{
                  previews.strategyDetails.value[
                    displayedStrategyVersion(group).package_hash
                  ]?.description || "暂无构图说明"
                }}</span>
                <span class="guide-choice-meta"
                  ><span
                    >v{{ displayedStrategyVersion(group).strategy_version
                    }}<template v-if="group.versions.length > 1">
                      · {{ group.versions.length }} 个版本</template
                    ></span
                  ><span
                    v-if="!displayedStrategyVersion(group).executable"
                    class="guide-trust"
                    >需确认信任</span
                  ></span
                >
              </button>
            </div>
            <p v-if="!strategyGroups.length" class="planting-empty">
              还没有已导入的种植策略。
            </p>
            <div
              v-if="planting.strategyDetail.value"
              class="guide-strategy-detail"
            >
              <div>
                <strong>{{
                  planting.selectedStrategy.value?.display_name
                }}</strong>
                <p>{{ planting.strategyDetail.value.description }}</p>
                <span>{{
                  roles.map((role) => role.display_name).join(" · ")
                }}</span>
              </div>
              <label
                v-if="
                  selectedStrategyGroup &&
                  selectedStrategyGroup.versions.length > 1
                "
                class="guide-version-select"
                ><span>版本</span
                ><select
                  :value="planting.selectedStrategyHash.value"
                  :disabled="busy"
                  @change="
                    selectStrategy(($event.target as HTMLSelectElement).value)
                  "
                >
                  <option
                    v-for="version in selectedStrategyGroup.versions"
                    :key="version.package_hash"
                    :value="version.package_hash"
                  >
                    v{{ version.strategy_version
                    }}{{ version.executable ? "" : " · 未信任" }}
                  </option>
                </select></label
              >
            </div>
          </template>

          <template v-else-if="editingStep === 'mapping'">
            <header class="guide-section-heading">
              <div>
                <span>02 / 植被搭配</span>
                <h3>
                  {{
                    mappingMode === "choose"
                      ? "选择本次使用的植被"
                      : "按角色选择植被"
                  }}
                </h3>
              </div>
              <button
                v-if="mappingMode === 'custom'"
                class="guide-text-button"
                type="button"
                :disabled="busy"
                @click="mappingMode = 'choose'"
              >
                <ArrowLeft :size="15" />返回搭配列表
              </button>
            </header>
            <template v-if="mappingMode === 'choose'">
              <div
                class="guide-profile-grid"
                role="radiogroup"
                aria-label="植被搭配"
              >
                <button
                  v-for="profile in planting.profiles.value"
                  :key="profile.profile_id"
                  class="guide-profile-card"
                  type="button"
                  role="radio"
                  :class="{
                    selected:
                      planting.selectedMappingKey.value === profile.profile_id,
                  }"
                  :aria-checked="
                    planting.selectedMappingKey.value === profile.profile_id
                  "
                  :disabled="busy"
                  @click="selectProfile(profile.profile_id)"
                >
                  <span class="guide-choice-heading"
                    ><strong>{{ profile.display_name }}</strong
                    ><CircleCheck
                      v-if="
                        planting.selectedMappingKey.value === profile.profile_id
                      "
                      :size="19" /><span v-else class="guide-radio"
                  /></span>
                  <span class="guide-profile-thumbnails"
                    ><span
                      v-for="plant in previews.profilePreviews.value[
                        profile.profile_id
                      ]?.plants.slice(0, 4)"
                      :key="plant.assetId"
                      class="guide-plant-image"
                      ><LibraryThumbnail
                        :resource-id="plant.resourceId"
                        :alt="plant.name"
                        ><Trees :size="22" /></LibraryThumbnail></span
                    ><span
                      v-if="
                        !previews.profilePreviews.value[profile.profile_id]
                          ?.plants.length
                      "
                      class="guide-plant-image"
                      ><Trees :size="22" /></span
                  ></span>
                  <span class="guide-profile-plants">{{
                    previews.profilePreviews.value[profile.profile_id]?.plants
                      .map((plant) => plant.name)
                      .join("、") || "已保存的植被搭配"
                  }}</span>
                  <span class="guide-choice-meta"
                    ><span
                      v-if="previews.profilePreviews.value[profile.profile_id]"
                      >{{
                        previews.profilePreviews.value[profile.profile_id]
                          .plants.length
                      }}
                      种植被 ·
                      {{
                        previews.profilePreviews.value[profile.profile_id]
                          .roleCount
                      }}
                      个角色</span
                    ><span v-else>读取植被信息…</span
                    ><span
                      v-if="
                        planting.selectedMappingKey.value === profile.profile_id
                      "
                      >{{
                        planting.mappingDirty.value ? "本次已调整" : "已选择"
                      }}</span
                    ></span
                  >
                </button>
                <button
                  class="guide-profile-card guide-custom-choice"
                  type="button"
                  :disabled="busy"
                  @click="startCustomMapping"
                >
                  <Plus :size="25" /><strong>{{
                    !planting.selectedProfile.value &&
                    Object.keys(planting.roleMappings.value).length
                      ? "继续自定义搭配"
                      : "自己搭配"
                  }}</strong
                  ><span>{{
                    planting.catalogs.value.length
                      ? "从资产库选择植被"
                      : "暂无可用资产库"
                  }}</span>
                </button>
              </div>
              <div
                v-if="
                  planting.selectedProfile.value || planting.mappingReady.value
                "
                class="guide-selection-tools"
              >
                <span
                  ><CircleCheck :size="16" />{{
                    planting.mappingSourceLabel.value
                  }}</span
                ><button
                  class="secondary-button"
                  type="button"
                  :disabled="busy"
                  @click="
                    mappingMode = 'custom';
                    focusFirstRole();
                  "
                >
                  <Pencil :size="15" />调整搭配
                </button>
              </div>
            </template>
            <template v-else>
              <p v-if="!planting.catalogs.value.length" class="guide-notice">
                暂无植被资产库。可先在资产库中添加植被，已有搭配仍可直接使用。
              </p>
              <div class="guide-role-progress">
                <span>角色用于区分空间构图中的植被关系</span
                ><strong>{{ configuredRoleCount }} 个已配置</strong>
              </div>
              <RoleMappingEditor
                guided
                :disabled="busy"
                :active-role-key="activeRoleKey"
                :roles="roles"
                :assets="planting.assets.value"
                :catalogs="planting.catalogs.value"
                :collections="planting.collections.value"
                :mappings="planting.roleMappings.value"
                :enabled="planting.roleEnabled.value"
                @update:active-role-key="activeRoleKey = $event"
                @update:enabled="planting.setRoleEnabled"
                @update:mappings="planting.replaceRoleMappings"
              />
            </template>
            <div
              v-if="missingRoles.length && mappingMode === 'custom'"
              class="guide-missing-roles"
              role="status"
            >
              <span>还需配置</span
              ><button
                v-for="role in missingRoles"
                :key="role.role_key"
                type="button"
                @click="focusRole(role.role_key)"
              >
                {{ role.display_name }}<ArrowRight :size="13" />
              </button>
            </div>
            <div
              v-if="planting.syncableProfileAssetCount.value"
              class="mapping-sync-notice"
            >
              <span
                >{{
                  planting.syncableProfileAssetCount.value
                }}
                项植被信息有更新</span
              ><button
                class="secondary-button"
                type="button"
                :disabled="
                  busy ||
                  !planting.managementAvailable.value ||
                  planting.mappingDirty.value
                "
                :title="
                  planting.mappingDirty.value
                    ? '请先保存本次搭配修改'
                    : '同步最新名称与参考半径'
                "
                @click="planting.syncSelectedProfileAssets"
              >
                <RefreshCw :size="14" />同步资产信息
              </button>
            </div>
            <p
              v-else-if="planting.profileAssetSyncMessage.value"
              class="planting-sync-success"
            >
              {{ planting.profileAssetSyncMessage.value }}
            </p>
          </template>

          <template v-else>
            <header class="guide-section-heading guide-effect-heading">
              <div>
                <h3>调整种植效果</h3>
                <p>
                  {{
                    planting.selectedProfile.value?.display_name ||
                    "本次自定义搭配"
                  }}
                  · {{ selectedPlants.length }} 种植被
                </p>
              </div>
              <div class="guide-effect-plants">
                <span
                  v-for="plant in selectedPlants.slice(0, 4)"
                  :key="plant.assetId"
                  class="guide-effect-thumb"
                  :title="plant.name"
                  ><LibraryThumbnail
                    :resource-id="plant.resourceId"
                    :alt="plant.name"
                    ><Trees :size="16" /></LibraryThumbnail
                ></span>
                <button
                  class="guide-text-button"
                  type="button"
                  :disabled="busy"
                  @click="editMapping"
                >
                  <Pencil :size="14" />调整搭配
                </button>
              </div>
            </header>
            <StrategyEffectWorkspace
              v-if="planting.strategyDetail.value"
              :strategy="planting.strategyDetail.value"
              :parameters="planting.visibleGenerationParameters.value"
              :values="planting.strategyParameterValues.value"
              :enabled="planting.roleEnabled.value"
              :random-seed="planting.randomSeed.value"
              :disabled="busy"
              :appearance="exampleAppearance"
              :example-cache="exampleCache"
              @update-parameter="planting.updateStrategyParameter"
              @update-random-seed="planting.updateRandomSeed"
              @reroll="rerollSeed"
              @reset="planting.resetStrategyParameters"
            />
          </template>
        </div>
      </section>
    </div>

    <footer class="planting-actions guide-actions">
      <button
        class="secondary-button"
        type="button"
        :disabled="busy"
        @click="emit('back')"
      >
        <ArrowLeft :size="15" />返回选区
      </button>
      <span class="guide-footer-status" aria-live="polite"
        ><LoaderCircle v-if="busy" :size="15" class="spinning" />{{
          generationStatus
        }}</span
      >
      <template v-if="entryMode === 'manual' && editingStep === 'strategy'"
        ><button
          class="primary-button"
          type="button"
          :disabled="
            busy ||
            !planting.strategyDetail.value ||
            !planting.selectedStrategy.value?.executable
          "
          @click="editingStep = 'mapping'"
        >
          下一步：选择植被<ArrowRight :size="15" /></button
      ></template>
      <template v-else-if="entryMode === 'manual' || entryMode === 'preset'">
        <button
          v-if="
            planting.execution.value?.status === 'failed' &&
            planting.execution.value.stage === 'assignment' &&
            planting.execution.value.generated_point_set_id
          "
          class="secondary-button"
          type="button"
          :disabled="busy"
          @click="planting.retryAssignment"
        >
          <RefreshCw :size="15" />重试植被分配
        </button>
        <button
          v-if="entryMode === 'manual' && editingStep === 'mapping'"
          class="primary-button"
          type="button"
          :disabled="busy || !planting.mappingReady.value"
          @click="editParameters"
        >
          <SlidersHorizontal :size="15" />调整效果
        </button>
        <button
          v-if="entryMode === 'manual' && editingStep === 'parameters'"
          class="secondary-button"
          type="button"
          :disabled="busy || !planting.canSaveProfile.value"
          @click="openSaveDialog"
        >
          <Save :size="15" />保存种植方案
        </button>
        <button
          v-if="entryMode === 'preset' || editingStep === 'parameters'"
          class="primary-button"
          type="button"
          :disabled="
            busy ||
            !planting.canSubmit.value ||
            (entryMode === 'preset' && !planting.selectedProfile.value)
          "
          @click="submitPreview"
        >
          <LoaderCircle v-if="generating" :size="17" class="spinning" /><Sprout
            v-else
            :size="17"
          />{{ generating ? "正在生成" : "开始种植" }}
        </button>
      </template>
    </footer>

    <div
      v-if="saveDialogOpen"
      class="studio-modal-backdrop"
      @mousedown.self="
        !planting.savingProfile.value && (saveDialogOpen = false)
      "
    >
      <section
        class="studio-modal profile-save-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="planting-save-title"
      >
        <header>
          <div>
            <span>种植方案</span>
            <h3 id="planting-save-title">保存种植方案</h3>
          </div>
          <button
            class="icon-button"
            type="button"
            title="关闭"
            :disabled="planting.savingProfile.value"
            @click="saveDialogOpen = false"
          >
            <X :size="17" />
          </button>
        </header>
        <label class="profile-name-field"
          ><span>名称</span
          ><input
            v-model.trim="saveDisplayName"
            type="text"
            placeholder="例如：项目常用乔灌木搭配"
            :disabled="planting.savingProfile.value"
        /></label>
        <div v-if="planting.selectedProfile.value" class="profile-save-mode">
          <label
            ><input
              v-model="saveAsNew"
              :value="false"
              type="radio"
              :disabled="planting.savingProfile.value"
            /><span
              ><strong>更新当前方案</strong
              ><small>保存本次植被搭配与种植效果。</small></span
            ></label
          ><label
            ><input
              v-model="saveAsNew"
              :value="true"
              type="radio"
              :disabled="planting.savingProfile.value"
            /><span
              ><strong>另存为新方案</strong
              ><small>保留当前方案并创建独立副本。</small></span
            ></label
          >
        </div>
        <p v-else class="profile-save-note">保存后可在其他种植批次中复用。</p>
        <footer>
          <button
            class="secondary-button"
            type="button"
            :disabled="planting.savingProfile.value"
            @click="saveDialogOpen = false"
          >
            取消</button
          ><button
            class="primary-button"
            type="button"
            :disabled="
              !saveDisplayName ||
              !planting.parametersValid.value ||
              planting.savingProfile.value || !planting.canSaveProfile.value
            "
            @click="saveProfile"
          >
            <LoaderCircle
              v-if="planting.savingProfile.value"
              :size="16"
              class="spinning"
            /><Save v-else :size="16" />保存
          </button>
        </footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.planting-guide {
  background: var(--color-surface);
  overflow: hidden;
}
.planting-guide .planting-progressive {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.planting-guide .guide-content {
  overflow-y: auto;
  border-bottom: 0;
}
.planting-header {
  align-items: center;
}
.planting-header h2 {
  font-size: 20px;
}
.guide-region-count {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-muted);
  font-size: 12px;
  white-space: nowrap;
}
.guide-region-count strong {
  color: var(--color-text);
  font-size: 20px;
}
.guide-steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  padding: 0 24px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-soft);
  flex: 0 0 auto;
}
.guide-steps button {
  display: flex;
  gap: 11px;
  align-items: center;
  min-width: 0;
  min-height: 78px;
  padding: 14px 16px;
  text-align: left;
  background: transparent;
  color: var(--color-text-muted);
  border: 0;
  border-bottom: 2px solid transparent;
  cursor: pointer;
}
.guide-steps button.active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
}
.guide-steps button:disabled {
  cursor: default;
}
.guide-steps button > span:nth-child(2) {
  flex: 1;
  min-width: 0;
}
.guide-steps small,
.guide-steps strong {
  display: block;
}
.guide-steps small {
  font-size: 11px;
  margin-bottom: 5px;
}
.guide-steps strong {
  font-size: 13px;
  overflow-wrap: anywhere;
  color: var(--color-text);
}
.guide-step-number {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  border: 1px solid var(--color-border-strong);
  border-radius: 50%;
  font-size: 12px;
  font-weight: 650;
}
.guide-steps .active .guide-step-number,
.guide-steps .complete .guide-step-number {
  background: var(--color-accent);
  color: white;
  border-color: var(--color-accent);
}
.guide-content {
  padding: 26px 28px;
}
.guide-main {
  width: min(980px, 100%);
  margin: 0 auto;
}
.guide-section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.guide-section-heading > div > span {
  font-size: 11px;
  color: var(--color-text-faint);
}
.guide-section-heading h3 {
  font-size: 18px;
  margin: 7px 0 0;
}
.guide-strategy-grid,
.guide-profile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr));
  gap: 12px;
}
.guide-strategy-card,
.guide-profile-card {
  display: flex;
  flex-direction: column;
  gap: 15px;
  min-width: 0;
  padding: 18px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
}
.guide-strategy-card {
  min-height: 180px;
}
.guide-strategy-card:hover:not(:disabled),
.guide-profile-card:hover:not(:disabled) {
  border-color: var(--color-accent);
}
.guide-strategy-card.selected,
.guide-profile-card.selected {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}
.guide-choice-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
}
.guide-choice-heading strong {
  flex: 1;
  font-size: 15px;
  overflow-wrap: anywhere;
}
.guide-choice-heading > svg {
  flex: 0 0 auto;
  color: var(--color-accent);
}
.guide-choice-icon {
  color: var(--color-accent);
  flex: 0 0 auto;
}
.guide-radio {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
  border: 1px solid var(--color-border-strong);
  border-radius: 50%;
}
.guide-strategy-description {
  color: var(--color-text-muted);
  font-size: 12px;
  line-height: 1.7;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 61px;
}
.guide-choice-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  justify-content: space-between;
  font-size: 11px;
  color: var(--color-text-faint);
  margin-top: auto;
}
.guide-trust {
  color: var(--color-warning);
}
.guide-strategy-detail {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-top: 24px;
  padding: 3px 0 3px 16px;
  border-left: 3px solid var(--color-accent);
}
.guide-strategy-detail > div {
  flex: 1;
  min-width: 0;
}
.guide-strategy-detail strong {
  font-size: 14px;
}
.guide-strategy-detail p {
  margin: 7px 0;
  color: var(--color-text-muted);
  line-height: 1.7;
  font-size: 12px;
}
.guide-strategy-detail > div > span {
  color: var(--color-text-faint);
  font-size: 11px;
}
.guide-version-select {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--color-text-muted);
}
.guide-version-select select,
.guide-catalog-row select {
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid var(--color-border-strong);
  border-radius: 4px;
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
}
.guide-profile-card {
  min-height: 211px;
}
.guide-profile-thumbnails {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
}
.guide-plant-image {
  display: grid;
  aspect-ratio: 1;
  min-width: 0;
  place-items: center;
  background: var(--color-surface-soft);
  border-radius: 4px;
  overflow: hidden;
  color: var(--color-accent);
}
.guide-plant-image :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.guide-profile-plants {
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-muted);
  overflow-wrap: anywhere;
}
.guide-custom-choice {
  align-items: center;
  justify-content: center;
  border-style: dashed;
  text-align: center;
  color: var(--color-accent);
}
.guide-custom-choice strong {
  font-size: 15px;
}
.guide-custom-choice > span {
  color: var(--color-text-muted);
  font-size: 12px;
}
.guide-selection-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 0;
}
.guide-selection-tools > span {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--color-accent);
}
.guide-text-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 0;
  border: 0;
  color: var(--color-accent);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}
.guide-text-button:disabled {
  color: var(--color-text-faint);
  cursor: default;
}
.guide-catalog-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.guide-catalog-row label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(360px, 100%);
  min-width: 0;
  font-size: 12px;
  color: var(--color-text-muted);
}
.guide-role-progress {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 14px;
  font-size: 12px;
  color: var(--color-text-muted);
}
.guide-role-progress strong {
  font-weight: 500;
  color: var(--color-accent);
}
.guide-notice {
  padding: 12px 0;
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.6;
}
.guide-missing-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
  align-items: center;
  margin-top: 18px;
  padding: 12px 0;
  border-top: 1px solid var(--color-border);
  font-size: 12px;
  color: var(--color-warning);
}
.guide-missing-roles button {
  display: flex;
  gap: 5px;
  align-items: center;
  border: 0;
  background: transparent;
  color: var(--color-accent);
  padding: 4px 0;
  cursor: pointer;
  font: inherit;
}
.guide-effect-heading {
  padding-bottom: 18px;
  border-bottom: 1px solid var(--color-border);
}
.guide-effect-heading h3 {
  margin: 0;
}
.guide-effect-heading p {
  margin: 7px 0 0;
  font-size: 11px;
  color: var(--color-text-muted);
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.guide-effect-plants {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.guide-effect-thumb {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  border-radius: 4px;
  overflow: hidden;
  color: var(--color-accent);
  background: var(--color-surface-soft);
}
.guide-effect-thumb :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.guide-effect-plants button {
  margin-left: 7px;
  flex: 0 0 auto;
}
.guide-actions {
  flex: 0 0 auto;
  flex-wrap: wrap;
  gap: 10px;
}
.guide-actions .guide-footer-status {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: 1;
  min-width: 100px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.guide-strategy-card:focus-visible,
.guide-profile-card:focus-visible,
.guide-steps button:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 3px;
}
@media (max-width: 760px) {
  .guide-content {
    padding: 20px 16px;
  }
  .guide-steps {
    padding: 0 8px;
  }
  .guide-steps button {
    padding: 12px 6px;
    gap: 7px;
  }
  .guide-steps button > svg {
    display: none;
  }
  .guide-steps strong {
    font-size: 11px;
  }
  .guide-step-number {
    width: 24px;
    height: 24px;
    flex-basis: 24px;
  }
  .guide-strategy-detail,
  .guide-catalog-row {
    flex-wrap: wrap;
  }
  .guide-effect-heading {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .guide-effect-thumb:nth-child(n + 3) {
    display: none;
  }
  .guide-actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    padding: 12px;
  }
  .guide-actions .guide-footer-status {
    order: -1;
    grid-column: 1 / -1;
  }
  .guide-actions .primary-button {
    grid-column: 1 / -1;
  }
}
</style>
