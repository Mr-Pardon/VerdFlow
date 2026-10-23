<script setup lang="ts">
import { Maximize2, RotateCcw, Trash2 } from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";

import type { PlantingCompositionSummary } from "../../api";
import RegionCanvas from "./RegionCanvas.vue";
import { useRegionViewer } from "./useRegionViewer";
import type { RegionSelectionMode } from "./useRegionViewer";

const props = defineProps<{
  sessionId: string;
  regionSetId: string;
  initialSelectedRegionIds: string[];
  composition: PlantingCompositionSummary | null;
  operationError?: string | null;
}>();

const emit = defineEmits<{
  selectionChanged: [regionIds: string[]];
  continue: [];
}>();

const viewer = useRegionViewer(props.sessionId, props.regionSetId);
const canvas = ref<{ fit: () => void; resetRotation: () => void } | null>(null);
const activeFilter = ref("all");
const committedRegionIds = computed(
  () => props.composition?.occupied_region_ids ?? [],
);
const committedRegionSet = computed(
  () => new Set(committedRegionIds.value),
);
const committedPointCount = computed(() =>
  (props.composition?.batches ?? []).reduce(
    (total, batch) => total + batch.point_count,
    0,
  ),
);

const filters = computed(() => [
  { value: "all", label: "全部" },
  ...(viewer.manifest.value?.classifications
    .filter((item) => item.classification !== null)
    .map((item) => ({
      value: item.classification as string,
      label: classificationLabel(item.classification),
    })) ?? []),
]);

const visibleSelectedArea = computed(() => {
  const selected = new Set(viewer.selectedRegionIds.value);
  return viewer.regions.value.reduce(
    (total, region) =>
      selected.has(region.region_id)
        ? total + (region.footprint_area_xy ?? 0)
        : total,
    0,
  );
});

const selectFilter = async (value: string) => {
  activeFilter.value = value;
  await viewer.setClassificationFilter(
    value === "all" ? [] : [value],
  );
};

const applySelection = (payload: {
  ids: string[];
  mode: RegionSelectionMode;
}) => {
  viewer.applySelection(
    payload.ids.filter((id) => !committedRegionSet.value.has(id)),
    payload.mode,
  );
};

const reload = async () => {
  await viewer.initialize();
  canvas.value?.fit();
};

watch(viewer.selectedRegionIds, (value) => emit("selectionChanged", value));
watch(committedRegionIds, () => {
  const available = viewer.selectedRegionIds.value.filter(
    (id) => !committedRegionSet.value.has(id),
  );
  if (available.length !== viewer.selectedRegionIds.value.length) {
    viewer.applySelection(available, "replace");
  }
});

onMounted(() => {
  viewer.applySelection(
    props.initialSelectedRegionIds.filter(
      (id) => !committedRegionSet.value.has(id),
    ),
    "replace",
  );
  void viewer.initialize();
});

const classificationLabel = (value: string | null) => {
  if (value?.includes("street")) return "行道树绿地";
  if (value?.includes("general")) return "普通绿地";
  return value || "未分类";
};

const formatArea = (value: number) =>
  new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value);

</script>

<template>
  <section class="region-stage">
    <div class="region-viewer-area">
      <RegionCanvas
        ref="canvas"
        :bounds="viewer.manifest.value?.bounds || null"
        :regions="viewer.regions.value"
        :selected-region-ids="viewer.selectedRegionIds.value"
        :committed-region-ids="committedRegionIds"
        :loading="viewer.loadingGeometry.value"
        @viewport="viewer.loadViewport"
        @selection="applySelection"
      />
      <div class="region-viewer-actions">
        <button type="button" title="重置画幅旋转" @click="canvas?.resetRotation()">
          <RotateCcw :size="17" />
        </button>
        <button type="button" title="适配视图" @click="canvas?.fit()">
          <Maximize2 :size="17" />
        </button>
      </div>
      <div v-if="viewer.geometryCapped.value" class="region-viewer-notice">
        当前画幅数据量已达显示上限，请放大后继续查看。
      </div>
    </div>

    <aside class="region-inspector">
      <header>
        <span>区域选择</span>
        <h2>选择种植范围</h2>
      </header>

      <div v-if="viewer.errorMessage.value" class="region-error" role="alert">
        <p>{{ viewer.errorMessage.value }}</p>
        <button type="button" @click="reload">
          <RotateCcw :size="15" />
          重试
        </button>
      </div>
      <div v-if="operationError" class="region-error" role="alert">
        <p>{{ operationError }}</p>
      </div>

      <dl class="region-stats">
        <div>
          <dt>区域</dt>
          <dd>{{ viewer.manifest.value?.region_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>已选</dt>
          <dd>{{ viewer.selectedRegionIds.value.length }}</dd>
        </div>
        <div>
          <dt>已存批次</dt>
          <dd>{{ composition?.batches.length ?? 0 }}</dd>
        </div>
      </dl>

      <section class="region-filter">
        <span>类型筛选</span>
        <div>
          <button
            v-for="filter in filters"
            :key="filter.value"
            type="button"
            :class="{ active: activeFilter === filter.value }"
            @click="selectFilter(filter.value)"
          >
            {{ filter.label }}
          </button>
        </div>
      </section>

      <section class="region-selection-summary">
        <span>当前选择</span>
        <strong>{{ viewer.selectedRegionIds.value.length }} 个区域</strong>
        <p>当前可见选择面积 {{ formatArea(visibleSelectedArea) }} m²</p>
      </section>

      <section v-if="composition?.batches.length" class="region-committed-summary">
        <strong>
          已保存 {{ composition.batches.length }} 批，
          {{ committedPointCount.toLocaleString() }} 个点位
        </strong>
        <p>灰色虚线区域已完成种植，不会再次进入框选；批次管理位于点位预览。</p>
      </section>

      <footer>
        <button
          class="secondary-button"
          type="button"
          :disabled="viewer.selectedRegionIds.value.length === 0"
          @click="viewer.clearSelection"
        >
          <Trash2 :size="16" />
          清空选择
        </button>
        <button
          class="primary-button"
          type="button"
          :disabled="viewer.selectedRegionIds.value.length === 0"
          @click="emit('continue')"
        >
          进入种植
        </button>
      </footer>
    </aside>
  </section>
</template>
