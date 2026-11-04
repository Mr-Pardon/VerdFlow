<script setup lang="ts">
import { computed } from "vue";
import {
  Crosshair,
  X,
  Plus,
  LoaderCircle,
  Upload,
  Download,
  CheckCheck,
  SquareDashed,
  Trash2,
  Eye,
  EyeOff,
  ChevronLeft,
  ChevronRight,
} from "@lucide/vue";
import type { useRegionDrafts } from "../features/useRegionDrafts";
import { geometryId, selectionId } from "../shared/regionLimits";
const props = defineProps<{ controller: ReturnType<typeof useRegionDrafts>; sdkReady: boolean }>();
const {
  drawing,
  pathMode,
  exportingRegions,
  importingRegions,
  clearingRegions,
  updatingRegionVisibility,
  clearRegionsDialog,
  regionExportNotice,
  regionExportPath,
  regions,
  regionSearch,
  regionPage,
  focusedRegionId,
  filteredRegions,
  regionPageCount,
  visibleRegions,
  samplingFailures,
  failureByRegion,
  revealRegion,
  selectFailedRegions,
  selectionLocked,
  enabledRegionCount,
  hasVisibleRegions,
  selectAllRegions,
  requestClearRegions,
  clearRegions,
  exportRegions,
  importRegions,
  startDrawing,
  cancelDrawing,
  toggleRegionPreview,
  toggleAllRegionPreviews,
  locateRegion,
  removeRegion,
} = props.controller;
const geometryName = computed(() => pathMode.value ? "路径" : "选区");
const visibilityLabel = computed(() => `${hasVisibleRegions.value ? '隐藏' : '显示'}全部${geometryName.value}`);
</script>

<template>
<section class="panel-section region-section">
        <div class="section-heading">
          <div><span class="section-index"><Crosshair :size="13" /></span><strong>{{ pathMode ? '种植路径' : '种植选区' }}</strong></div>
          <span class="section-heading-actions">{{ enabledRegionCount }} / {{ regions.length }} {{ pathMode ? '条路径' : '个面' }}</span>
        </div>
        <div class="region-draw-actions">
          <button class="secondary-button" :disabled="!sdkReady || selectionLocked" @click="drawing ? cancelDrawing() : startDrawing()">
            <X v-if="drawing" :size="14" /><Plus v-else :size="14" /> {{ drawing ? "取消绘制" : pathMode ? "绘制路径" : "绘制选区" }}
          </button>
          <button v-if="!pathMode" class="secondary-button" :disabled="!sdkReady || selectionLocked || drawing" @click="importRegions" title="导入 SHP 或 SHP ZIP">
            <LoaderCircle v-if="importingRegions" :size="14" /><Upload v-else :size="14" />{{ importingRegions ? "正在导入" : "导入 SHP" }}
          </button>
          <button v-if="!pathMode" class="secondary-button" :disabled="selectionLocked || drawing || !enabledRegionCount" @click="exportRegions" title="导出勾选面为 SHP ZIP">
            <LoaderCircle v-if="exportingRegions" :size="14" /><Download v-else :size="14" />{{ exportingRegions ? "等待保存" : "导出 SHP" }}
          </button>
        </div>
        <div v-if="regions.length" class="region-bulk-actions">
          <button :disabled="selectionLocked || drawing || enabledRegionCount === regions.length" :title="`全选所有分页的${geometryName}`" @click="selectAllRegions()"><CheckCheck :size="14" />全选</button>
          <button :disabled="selectionLocked || drawing" :title="`反选所有分页的${geometryName}`" @click="selectAllRegions(true)"><SquareDashed :size="14" />反选</button>
          <button class="region-visibility-toggle" :disabled="!sdkReady || selectionLocked || drawing" :title="visibilityLabel" :aria-label="visibilityLabel" :aria-pressed="hasVisibleRegions" @click="toggleAllRegionPreviews">
            <LoaderCircle v-if="updatingRegionVisibility" :size="15" class="spin" /><Eye v-else-if="hasVisibleRegions" :size="15" /><EyeOff v-else :size="15" />
          </button>
          <button class="region-clear" :disabled="selectionLocked || drawing" title="清空当前类型的选区" @click="requestClearRegions"><Trash2 :size="14" />{{ clearingRegions ? "正在清空" : pathMode ? "清空路径" : "清空选面" }}</button>
        </div>
        <input v-if="regions.length > 10" v-model="regionSearch" class="region-search" :aria-label="`搜索${geometryName}`" :placeholder="`搜索${geometryName}名称或编号`" />
        <div v-if="regions.length" class="region-list">
          <div v-for="region in visibleRegions" :id="`region-row-${geometryId(region)}`" :key="geometryId(region)" class="region-row" :class="{ excluded: !region.enabled, focused: focusedRegionId === geometryId(region), 'sampling-failed': failureByRegion.has(geometryId(region)) }">
            <label><input v-model="region.enabled" type="checkbox" :disabled="selectionLocked" :aria-label="`参与种植：${region.name}`" />
              <span><strong :title="region.name">{{ region.name }}</strong><small :title="failureByRegion.get(geometryId(region))">{{ failureByRegion.has(geometryId(region)) ? "采样失败" : `${region.coordinates.length} 个顶点` }}</small></span>
            </label>
            <button class="icon-button" :disabled="selectionLocked" :title="`定位${region.name}`" @click="locateRegion(region)"><Crosshair :size="15" /></button>
            <button class="icon-button" :disabled="selectionLocked" :title="`${region.visible ? '隐藏' : '显示'}${geometryName}`" @click="toggleRegionPreview(region)">
              <Eye v-if="region.visible" :size="15" /><EyeOff v-else :size="15" />
            </button>
            <button class="icon-button region-remove" :disabled="selectionLocked" :title="`移除${region.name}`" @click="removeRegion(region)"><Trash2 :size="15" /></button>
          </div>
        </div>
        <div v-if="regionPageCount > 1" class="region-pagination">
          <button class="icon-button" :disabled="regionPage === 0" title="上一页" @click="regionPage--"><ChevronLeft :size="15" /></button>
          <span>{{ regionPage + 1 }} / {{ regionPageCount }} · {{ filteredRegions.length }} {{ pathMode ? '条路径' : '个面' }}</span>
          <button class="icon-button" :disabled="regionPage + 1 >= regionPageCount" title="下一页" @click="regionPage++"><ChevronRight :size="15" /></button>
        </div>
        <details v-if="samplingFailures.length" class="region-failures">
          <summary>{{ samplingFailures.length }} {{ pathMode ? '条路径' : '个面' }}采样失败</summary>
          <button class="secondary-button" :disabled="selectionLocked" @click="selectFailedRegions">仅选失败项</button>
          <ul><li v-for="failure in samplingFailures" :key="selectionId(failure)"><button @click="revealRegion(selectionId(failure))">{{ failure.name }}</button><span>{{ failure.reason }}</span></li></ul>
        </details>
        <div v-if="regionExportNotice" class="region-export-notice" role="status" :title="regionExportPath ?? undefined">
          <span>{{ regionExportNotice }}</span>
          <button type="button" class="icon-button" title="关闭提示" aria-label="关闭提示" @click="regionExportNotice = ''; regionExportPath = null"><X :size="14" /></button>
        </div>
      </section>
<dialog ref="clearRegionsDialog" class="region-clear-dialog" aria-labelledby="clear-regions-title" aria-describedby="clear-regions-description">
    <h2 id="clear-regions-title">清空全部{{ geometryName }}？</h2>
    <p id="clear-regions-description">将移除全部 {{ regions.length }} {{ pathMode ? '条路径' : '个选区' }}及对应的场景预览，无法撤销。已种植的植被不受影响。</p>
    <div class="region-clear-dialog-actions">
      <button class="secondary-button" autofocus @click="clearRegionsDialog?.close()">取消</button>
      <button class="secondary-button danger-button" :disabled="selectionLocked || drawing" @click="clearRegions"><Trash2 :size="15" />确认清空</button>
    </div>
  </dialog>
</template>
