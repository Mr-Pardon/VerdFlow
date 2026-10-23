<script setup lang="ts">
import { ChevronRight, FileBox, Map, X } from "@lucide/vue";

defineProps<{
  fbxAvailable: boolean;
  geospatialAvailable: boolean;
}>();

defineEmits<{
  close: [];
  selectFbx: [];
  selectGeospatial: [];
}>();
</script>

<template>
  <div class="dialog-backdrop" @mousedown.self="$emit('close')">
    <section
      class="creation-dialog source-mode-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="source-mode-title"
    >
      <header>
        <div>
          <span>DTS · 项目工作台</span>
          <h2 id="source-mode-title">选择区域来源</h2>
        </div>
        <button type="button" title="关闭" @click="$emit('close')">
          <X :size="19" />
        </button>
      </header>

      <div class="creation-dialog__body source-mode-options">
        <button
          class="source-mode-option"
          type="button"
          :disabled="!fbxAvailable"
          @click="$emit('selectFbx')"
        >
          <span class="source-mode-option__icon"><FileBox :size="22" /></span>
          <span class="source-mode-option__copy">
            <strong>FBX 模型</strong>
            <small>从模型材质提取种植区域</small>
          </span>
          <span class="source-mode-option__state">当前可用</span>
          <ChevronRight :size="18" />
        </button>

        <button
          class="source-mode-option"
          type="button"
          :disabled="!geospatialAvailable"
          @click="$emit('selectGeospatial')"
        >
          <span class="source-mode-option__icon"><Map :size="22" /></span>
          <span class="source-mode-option__copy">
            <strong>SHP / GeoJSON</strong>
            <small>导入区域并匹配 GeoTIFF 高程</small>
          </span>
          <span class="source-mode-option__state">
            当前可用
          </span>
          <ChevronRight :size="18" />
        </button>
      </div>

      <footer>
        <button class="secondary-button" type="button" @click="$emit('close')">
          取消
        </button>
      </footer>
    </section>
  </div>
</template>
