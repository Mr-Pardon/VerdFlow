<script setup lang="ts">
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
const props = defineProps<{ controller: ReturnType<typeof useRegionDrafts>; sdkReady: boolean }>();
const {
  drawing,
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
</script>

<template>
<section class="panel-section region-section">
        <div class="section-heading">
          <div><span class="section-index"><Crosshair :size="13" /></span><strong>种植选区</strong></div>
          <span class="section-heading-actions">{{ enabledRegionCount }} / {{ regions.length }} 个面</span>
        </div>
        <div class="region-draw-actions">
          <button class="secondary-button" :disabled="!sdkReady || selectionLocked" @click="drawing ? cancelDrawing() : startDrawing()">
            <X v-if="drawing" :size="14" /><Plus v-else :size="14" /> {{ drawing ? "取消绘制" : "绘制选区" }}
          </button>
          <button class="secondary-button" :disabled="!sdkReady || selectionLocked || drawing" @click="importRegions" title="导入 SHP 或 SHP ZIP">
            <LoaderCircle v-if="importingRegions" :size="14" /><Upload v-else :size="14" />{{ importingRegions ? "正在导入" : "导入 SHP" }}
          </button>
          <button class="secondary-button" :disabled="selectionLocked || drawing || !enabledRegionCount" @click="exportRegions" title="导出勾选面为 SHP ZIP">
            <LoaderCircle v-if="exportingRegions" :size="14" /><Download v-else :size="14" />{{ exportingRegions ? "等待保存" : "导出 SHP" }}
          </button>
        </div>
        <div v-if="regions.length" class="region-bulk-actions">
          <button :disabled="selectionLocked || drawing || enabledRegionCount === regions.length" title="全选所有分页的选区" @click="selectAllRegions()"><CheckCheck :size="14" />全选</button>
          <button :disabled="selectionLocked || drawing" title="反选所有分页的选区" @click="selectAllRegions(true)"><SquareDashed :size="14" />反选</button>
          <button class="region-visibility-toggle" :disabled="!sdkReady || selectionLocked || drawing" :title="hasVisibleRegions ? '隐藏全部选区' : '显示全部选区'" :aria-label="hasVisibleRegions ? '隐藏全部选区' : '显示全部选区'" :aria-pressed="hasVisibleRegions" @click="toggleAllRegionPreviews">
            <LoaderCircle v-if="updatingRegionVisibility" :size="15" class="spin" /><Eye v-else-if="hasVisibleRegions" :size="15" /><EyeOff v-else :size="15" />
          </button>
          <button class="region-clear" :disabled="selectionLocked || drawing" title="清空全部选区" @click="requestClearRegions"><Trash2 :size="14" />{{ clearingRegions ? "正在清空" : "清空选面" }}</button>
        </div>
        <input v-if="regions.length > 10" v-model="regionSearch" class="region-search" aria-label="搜索选区" placeholder="搜索选区名称或编号" />
        <div v-if="regions.length" class="region-list">
          <div v-for="region in visibleRegions" :id="`region-row-${region.region_id}`" :key="region.region_id" class="region-row" :class="{ excluded: !region.enabled, focused: focusedRegionId === region.region_id, 'sampling-failed': failureByRegion.has(region.region_id) }">
            <label><input v-model="region.enabled" type="checkbox" :disabled="selectionLocked" :aria-label="`参与种植：${region.name}`" />
              <span><strong :title="region.name">{{ region.name }}</strong><small :title="failureByRegion.get(region.region_id)">{{ failureByRegion.has(region.region_id) ? "采样失败" : `${region.coordinates.length} 个顶点` }}</small></span>
            </label>
            <button class="icon-button" :disabled="selectionLocked" :title="`定位${region.name}`" @click="locateRegion(region)"><Crosshair :size="15" /></button>
            <button class="icon-button" :disabled="selectionLocked" :title="region.visible ? '隐藏选区' : '显示选区'" @click="toggleRegionPreview(region)">
              <Eye v-if="region.visible" :size="15" /><EyeOff v-else :size="15" />
            </button>
            <button class="icon-button region-remove" :disabled="selectionLocked" :title="`移除${region.name}`" @click="removeRegion(region)"><Trash2 :size="15" /></button>
          </div>
        </div>
        <div v-if="regionPageCount > 1" class="region-pagination">
          <button class="icon-button" :disabled="regionPage === 0" title="上一页" @click="regionPage--"><ChevronLeft :size="15" /></button>
          <span>{{ regionPage + 1 }} / {{ regionPageCount }} · {{ filteredRegions.length }} 个面</span>
          <button class="icon-button" :disabled="regionPage + 1 >= regionPageCount" title="下一页" @click="regionPage++"><ChevronRight :size="15" /></button>
        </div>
        <details v-if="samplingFailures.length" class="region-failures">
          <summary>{{ samplingFailures.length }} 个面采样失败</summary>
          <button class="secondary-button" :disabled="selectionLocked" @click="selectFailedRegions">仅选失败面</button>
          <ul><li v-for="failure in samplingFailures" :key="failure.region_id"><button @click="revealRegion(failure.region_id)">{{ failure.name }}</button><span>{{ failure.reason }}</span></li></ul>
        </details>
        <p v-if="regionExportNotice" class="region-export-notice" role="status" :title="regionExportPath ?? undefined">{{ regionExportNotice }}</p>
      </section>
<dialog ref="clearRegionsDialog" class="region-clear-dialog" aria-labelledby="clear-regions-title" aria-describedby="clear-regions-description">
    <h2 id="clear-regions-title">清空全部选区？</h2>
    <p id="clear-regions-description">将移除全部 {{ regions.length }} 个选区及场景中的选区预览，无法撤销。已种植的植被不受影响。</p>
    <div class="region-clear-dialog-actions">
      <button class="secondary-button" autofocus @click="clearRegionsDialog?.close()">取消</button>
      <button class="secondary-button danger-button" :disabled="selectionLocked || drawing" @click="clearRegions"><Trash2 :size="15" />确认清空</button>
    </div>
  </dialog>
</template>
