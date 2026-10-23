<script setup lang="ts">
import {
  ChevronDown,
  ChevronRight,
  FileOutput,
  LoaderCircle,
  LocateFixed,
  RefreshCw,
  RotateCcw,
  Sprout,
  Trash2,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";

import type { PlantingBatchSummary, PointReviewManifest } from "../../api";
import type { PreviewBatchSelection } from "./compositionOutput";
import PointReviewCanvas from "./PointReviewCanvas.vue";
import { usePointReview } from "./usePointReview";

const props = defineProps<{
  sessionId: string;
  currentExecutionId?: string | null;
  pointSetId?: string | null;
  assignmentSetId?: string | null;
  currentRegionCount?: number;
  currentSelectedRegionIds?: string[];
  currentSelectedAssetIds?: string[] | null;
  selectionLoading?: boolean;
  selectionError?: string | null;
  committedBatches: PlantingBatchSummary[];
  initialSelections?: PreviewBatchSelection[];
  committing?: boolean;
  commitError?: string | null;
  removingBatchId?: string | null;
  replantingBatchId?: string | null;
  replacingBatchId?: string | null;
}>();

const emit = defineEmits<{
  back: [];
  continuePlanting: [selectedAssetIds: string[] | null];
  outputPreview: [selections: PreviewBatchSelection[]];
  removeBatch: [batchId: string];
  replantBatch: [batchId: string];
  "update:currentSelectedAssetIds": [value: string[] | null];
}>();

interface ReviewBatchDefinition {
  key: string;
  batchId: string | null;
  executionId: string;
  ordinal: number;
  strategyId: string | null;
  strategyVersion: string | null;
  regionCount: number;
  selectedRegionIds: string[];
  status: string;
  stage: string;
  manifest: PointReviewManifest;
  eligibleAssetIds: string[];
}

const canvas = ref<InstanceType<typeof PointReviewCanvas> | null>(null);
const currentCommittedBatch = props.committedBatches.find(
  (batch) => batch.plant_run_execution_id === props.currentExecutionId,
);
const draftKey = props.currentExecutionId
  ? `draft:${props.currentExecutionId}`
  : null;
const visibleCommittedBatches = props.committedBatches.filter(
  (batch) =>
    !(
      draftKey &&
      !currentCommittedBatch &&
      props.replacingBatchId === batch.batch_id
    ),
);
const sources = visibleCommittedBatches.map((batch) => ({
  key: batch.batch_id,
  pointSetId: batch.generated_point_set_id,
  assignmentSetId: batch.asset_assignment_set_id,
}));
const replacingBatchOrdinal = props.replacingBatchId
  ? props.committedBatches.find(
      (batch) => batch.batch_id === props.replacingBatchId,
    )?.ordinal ?? null
  : null;
if (
  draftKey &&
  props.pointSetId &&
  props.assignmentSetId &&
  !currentCommittedBatch
) {
  sources.push({
    key: draftKey,
    pointSetId: props.pointSetId,
    assignmentSetId: props.assignmentSetId,
  });
}

const review = usePointReview({ sessionId: props.sessionId, sources });
const selectedBySource = ref<Record<string, string[]>>({});
const initializedSources = new Set<string>();
const expandedKeys = ref(
  new Set(sources.length ? [sources[sources.length - 1].key] : []),
);
const showReferenceRanges = ref(true);

const batchDefinitions = computed<ReviewBatchDefinition[]>(() => {
  const committed = visibleCommittedBatches.flatMap((batch) => {
    const manifest = review.manifests.value[batch.batch_id];
    if (!manifest) return [];
    const base = batch.selected_asset_ids
      ? new Set(batch.selected_asset_ids)
      : null;
    return [{
      key: batch.batch_id,
      batchId: batch.batch_id,
      executionId: batch.plant_run_execution_id,
      ordinal: batch.ordinal,
      strategyId: batch.strategy_id,
      strategyVersion: batch.strategy_version,
      regionCount: batch.selected_region_ids.length,
      selectedRegionIds: batch.selected_region_ids,
      status: batch.status,
      stage: batch.stage,
      manifest,
      eligibleAssetIds: manifest.assets
        .map((asset) => asset.key)
        .filter((assetId) => base === null || base.has(assetId)),
    }];
  });
  if (!draftKey || currentCommittedBatch) return committed;
  const manifest = review.manifests.value[draftKey];
  if (!manifest || !props.currentExecutionId) return committed;
  return [
    ...committed,
    {
      key: draftKey,
      batchId: null,
      executionId: props.currentExecutionId,
      ordinal: replacingBatchOrdinal ?? props.committedBatches.length,
      strategyId: null,
      strategyVersion: null,
      regionCount: props.currentRegionCount ?? 0,
      selectedRegionIds: props.currentSelectedRegionIds ?? [],
      status: "draft",
      stage: "review",
      manifest,
      eligibleAssetIds: manifest.assets.map((asset) => asset.key),
    },
  ];
});

watch(
  batchDefinitions,
  (definitions) => {
    const next = { ...selectedBySource.value };
    for (const batch of definitions) {
      if (initializedSources.has(batch.key)) continue;
      const saved = props.initialSelections?.find(
        (item) =>
          (batch.batchId !== null && item.batchId === batch.batchId) ||
          item.plantRunExecutionId === batch.executionId,
      );
      next[batch.key] = saved
        ? selectedFromSaved(saved.selectedAssetIds, batch.eligibleAssetIds)
        : batch.batchId === null
          ? selectedFromSaved(
              props.currentSelectedAssetIds ?? null,
              batch.eligibleAssetIds,
            )
          : [...batch.eligibleAssetIds];
      initializedSources.add(batch.key);
    }
    selectedBySource.value = next;
  },
  { immediate: true },
);

const selectedSet = (key: string) =>
  new Set(selectedBySource.value[key] ?? []);

const selectedPointCountFor = (batch: ReviewBatchDefinition) => {
  const selected = selectedSet(batch.key);
  return batch.manifest.assets.reduce(
    (total, asset) => total + (selected.has(asset.key) ? asset.point_count : 0),
    0,
  );
};

const selectedPointCount = computed(() =>
  batchDefinitions.value.reduce(
    (total, batch) => total + selectedPointCountFor(batch),
    0,
  ),
);
const totalPointCount = computed(() =>
  batchDefinitions.value.reduce(
    (total, batch) => total + batch.manifest.point_count,
    0,
  ),
);
const plantedRegionIds = computed(() => [
  ...new Set(
    batchDefinitions.value.flatMap((batch) => batch.selectedRegionIds),
  ),
]);
const visiblePoints = computed(() =>
  batchDefinitions.value.flatMap((batch) => {
    const selected = selectedSet(batch.key);
    const referenceRadii = new Map(
      batch.manifest.assets.map((asset) => [asset.key, asset.reference_radius_m]),
    );
    return (review.pointsBySource.value[batch.key] ?? [])
      .filter((point) => selected.has(point.asset_id))
      .map((point) => ({
        ...point,
        reference_radius_m: referenceRadii.get(point.asset_id) ?? null,
      }));
  }),
);
const hasReferenceRanges = computed(() =>
  batchDefinitions.value.some((batch) =>
    batch.manifest.assets.some((asset) => asset.reference_radius_m !== null),
  ),
);
const referenceAssetCount = computed(() =>
  batchDefinitions.value.reduce(
    (total, batch) =>
      total + batch.manifest.assets.filter(
        (asset) => asset.reference_radius_m !== null,
      ).length,
    0,
  ),
);
const hasDraft = computed(() =>
  batchDefinitions.value.some((batch) => batch.batchId === null),
);
const draftSelection = computed(() => {
  const draft = batchDefinitions.value.find((batch) => batch.batchId === null);
  return draft ? normalizedSelection(draft) : null;
});
const actionsBusy = computed(
  () =>
    review.loading.value ||
    props.selectionLoading ||
    props.committing ||
    Boolean(props.selectionError) ||
    Boolean(review.errorMessage.value),
);
const continueDisabled = computed(
  () =>
    actionsBusy.value ||
    (hasDraft.value && selectedPointCountForDraft() === 0),
);
const outputDisabled = computed(
  () =>
    actionsBusy.value ||
    selectedPointCount.value === 0,
);

const toggleExpanded = (key: string) => {
  const next = new Set(expandedKeys.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expandedKeys.value = next;
};

const toggleBatch = (batch: ReviewBatchDefinition) => {
  const selected = selectedSet(batch.key);
  updateSelection(
    batch,
    selected.size ? [] : [...batch.eligibleAssetIds],
  );
};

const toggleAsset = (batch: ReviewBatchDefinition, assetId: string) => {
  const selected = selectedSet(batch.key);
  if (selected.has(assetId)) selected.delete(assetId);
  else selected.add(assetId);
  updateSelection(batch, [...selected].sort());
};

const updateSelection = (
  batch: ReviewBatchDefinition,
  selectedAssetIds: string[],
) => {
  selectedBySource.value = {
    ...selectedBySource.value,
    [batch.key]: selectedAssetIds,
  };
  if (batch.batchId === null) {
    emit("update:currentSelectedAssetIds", normalizedSelection(batch));
  }
};

const normalizedSelection = (batch: ReviewBatchDefinition) => {
  const selected = selectedBySource.value[batch.key] ?? [];
  if (!selected.length) return [];
  return selected.length === batch.eligibleAssetIds.length
    ? null
    : [...selected].sort();
};

const selectedPointCountForDraft = () => {
  const draft = batchDefinitions.value.find((batch) => batch.batchId === null);
  return draft ? selectedPointCountFor(draft) : 0;
};

const outputSelections = () =>
  batchDefinitions.value.map((batch) => ({
    batchId: batch.batchId,
    plantRunExecutionId: batch.executionId,
    selectedAssetIds: normalizedSelection(batch),
    pointCount: selectedPointCountFor(batch),
  }));

const batchStatusLabel = (batch: ReviewBatchDefinition) =>
  batch.batchId ? "已保存" : "当前批次";

const batchPreparationLabel = (batch: ReviewBatchDefinition) =>
  ({
    draft: null,
    queued: "统一输出：高程采样排队中",
    running: "统一输出：正在采样高程",
    awaiting_confirmation: null,
    succeeded: "高程结果已缓存",
    failed: "高程采样失败",
    cancelled: "高程采样已取消",
    cancel_requested: "统一输出：正在停止采样",
    interrupted: "高程采样已中断",
  })[batch.status] ?? null;

const selectedFromSaved = (
  selected: string[] | null,
  eligible: string[],
) => {
  if (selected === null) return [...eligible];
  const allowed = new Set(eligible);
  return selected.filter((assetId) => allowed.has(assetId));
};

onMounted(() => void review.initialize());
</script>

<template>
  <section class="point-review-stage">
    <div class="point-review-viewer">
      <PointReviewCanvas
        ref="canvas"
        :bounds="review.reviewBounds.value"
        :regions="review.regions.value"
        :points="visiblePoints"
        :colors="review.colors.value"
        :loading="review.loading.value || review.loadingPoints.value || review.loadingRegions.value"
        :show-reference-ranges="showReferenceRanges"
        :point-details-visible="review.pointDetailsVisible.value"
        :planted-region-ids="plantedRegionIds"
        @viewport="review.loadViewport"
      />
      <div class="region-viewer-actions">
        <button type="button" title="适配全部点位" @click="canvas?.fit()">
          <LocateFixed :size="17" />
        </button>
        <button type="button" title="重置画幅旋转" @click="canvas?.resetRotation()">
          <RotateCcw :size="17" />
        </button>
      </div>
      <div
        v-if="!review.pointDetailsVisible.value || review.pointsCapped.value || review.regionsCapped.value"
        class="region-viewer-notice"
      >
        <template v-if="!review.pointDetailsVisible.value">种植面概览</template>
        <template v-else-if="review.pointsCapped.value">当前画幅点位预览已限流</template>
        <template v-else>当前画幅地形数据已达显示上限</template>
      </div>
    </div>

    <aside class="point-review-inspector">
      <header>
        <span>点位预览</span>
        <h2>种植批次与植被</h2>
      </header>
      <div v-if="review.errorMessage.value" class="region-error" role="alert">
        <p>{{ review.errorMessage.value }}</p>
        <button
          type="button"
          class="secondary-button"
          :disabled="review.loading.value || review.loadingPoints.value || review.loadingRegions.value"
          @click="review.retry"
        >
          <RefreshCw :size="15" />
          重试
        </button>
      </div>
      <p v-if="selectionError" class="region-error" role="alert">{{ selectionError }}</p>
      <p v-if="commitError" class="region-error" role="alert">{{ commitError }}</p>
      <dl class="point-review-stats">
        <div>
          <dt>提取面</dt>
          <dd>{{ (review.regionManifest.value?.region_count ?? 0).toLocaleString() }}</dd>
        </div>
        <div>
          <dt>种植批次</dt>
          <dd>{{ batchDefinitions.length }}</dd>
        </div>
        <div>
          <dt>全部点位</dt>
          <dd>{{ totalPointCount.toLocaleString() }}</dd>
        </div>
        <div>
          <dt>预览保留</dt>
          <dd>{{ selectedPointCount.toLocaleString() }}</dd>
        </div>
      </dl>

      <label class="point-review-range-toggle" :data-disabled="!hasReferenceRanges">
        <input
          v-model="showReferenceRanges"
          type="checkbox"
          :disabled="!hasReferenceRanges"
        />
        <span>显示参考范围</span>
        <small>{{ hasReferenceRanges ? `${referenceAssetCount} 类资产` : '未设置参考半径' }}</small>
      </label>

      <section class="point-review-batches">
        <span>批次筛选</span>
        <p>勾选结果只影响本次预览与输出，不修改已保存批次。</p>
        <article
          v-for="batch in batchDefinitions"
          :key="batch.key"
          class="point-review-batch"
          :data-enabled="(selectedBySource[batch.key]?.length ?? 0) > 0"
        >
          <header>
            <label title="保留或隐藏整个批次">
              <input
                type="checkbox"
                :checked="(selectedBySource[batch.key]?.length ?? 0) > 0"
                :disabled="selectionLoading"
                @change="toggleBatch(batch)"
              />
            </label>
            <button type="button" class="point-review-batch-toggle" @click="toggleExpanded(batch.key)">
              <span>
                <strong>
                  {{
                    batch.batchId
                      ? `第 ${batch.ordinal + 1} 批`
                      : replacingBatchOrdinal !== null
                        ? `第 ${batch.ordinal + 1} 批 · 待替换`
                        : "当前批次"
                  }}
                </strong>
                <small>
                  {{ batch.strategyId || "待保存种植结果" }}
                  <template v-if="batch.strategyVersion"> · {{ batch.strategyVersion }}</template>
                </small>
              </span>
              <i :data-status="batch.batchId ? 'saved' : batch.status">
                {{ batchStatusLabel(batch) }}
              </i>
              <ChevronDown v-if="expandedKeys.has(batch.key)" :size="16" />
              <ChevronRight v-else :size="16" />
            </button>
            <div v-if="batch.batchId" class="point-review-batch-actions">
              <button
                type="button"
                class="point-review-batch-replant"
                title="重新种植这一批"
                :disabled="
                  hasDraft ||
                  Boolean(removingBatchId) ||
                  Boolean(replantingBatchId)
                "
                @click="emit('replantBatch', batch.batchId)"
              >
                <LoaderCircle
                  v-if="replantingBatchId === batch.batchId"
                  :size="15"
                  class="spinning"
                />
                <RotateCcw v-else :size="15" />
              </button>
              <button
                type="button"
                class="point-review-batch-delete"
                title="删除这一批"
                :disabled="
                  Boolean(removingBatchId) ||
                  Boolean(replantingBatchId)
                "
                @click="emit('removeBatch', batch.batchId)"
              >
                <LoaderCircle
                  v-if="removingBatchId === batch.batchId"
                  :size="15"
                  class="spinning"
                />
                <Trash2 v-else :size="15" />
              </button>
            </div>
          </header>
          <div class="point-review-batch-summary">
            <span>{{ batch.regionCount }} 个区域</span>
            <span>{{ batch.manifest.point_count.toLocaleString() }} 个点位</span>
            <strong>{{ selectedPointCountFor(batch).toLocaleString() }} 个保留</strong>
          </div>
          <p
            v-if="batchPreparationLabel(batch)"
            class="point-review-batch-preparation"
            :data-status="batch.status"
          >
            {{ batchPreparationLabel(batch) }}
          </p>
          <div v-if="expandedKeys.has(batch.key)" class="point-review-assets">
            <label
              v-for="asset in batch.manifest.assets.filter((item) => batch.eligibleAssetIds.includes(item.key))"
              :key="asset.key"
              class="asset-output-choice"
            >
              <input
                type="checkbox"
                :checked="selectedSet(batch.key).has(asset.key)"
                :disabled="selectionLoading"
                @change="toggleAsset(batch, asset.key)"
              />
              <i :style="{ backgroundColor: review.colors.value[asset.key] }" />
              <span :title="asset.display_name ? `${asset.display_name} (${asset.key})` : asset.key">
                {{ asset.display_name || asset.key }}
              </span>
              <strong>{{ asset.point_count.toLocaleString() }}</strong>
            </label>
          </div>
        </article>
        <p v-if="!batchDefinitions.length">当前没有可预览的种植批次。</p>
      </section>

      <p class="point-review-help">滚轮缩放，中键平移，右键拖动旋转画幅。</p>
      <footer class="point-review-actions">
        <button
          v-if="hasDraft"
          class="secondary-button"
          type="button"
          :disabled="committing"
          @click="emit('back')"
        >
          返回种植配置
        </button>
        <button
          class="secondary-button"
          type="button"
          :disabled="continueDisabled"
          @click="emit('continuePlanting', draftSelection)"
        >
          <LoaderCircle v-if="committing" :size="17" class="spinning" />
          <Sprout v-else :size="17" />
          {{ committing ? "正在保存" : "继续种植" }}
        </button>
        <button
          class="primary-button"
          type="button"
          :disabled="outputDisabled"
          @click="emit('outputPreview', outputSelections())"
        >
          <FileOutput :size="17" />
          输出预览点位
        </button>
      </footer>
    </aside>
  </section>
</template>
