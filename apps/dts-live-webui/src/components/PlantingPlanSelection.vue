<script setup lang="ts">
import {
  ArrowLeft,
  ChevronRight,
  Pencil,
  Sprout,
  ChartScatter,
} from "@lucide/vue";
import { nextTick, ref, watch } from "vue";
import type { LiveConfiguration } from "../features/useLiveConfiguration";
import VegetationThumbnail from "./VegetationThumbnail.vue";
import PlantingPlanPicker from "./PlantingPlanPicker.vue";
const props = defineProps<{
  controller: LiveConfiguration;
  disabled: boolean;
  anchor: HTMLElement | null;
  pickerOpen: boolean;
  hasExample: boolean;
  exampleOpen: boolean;
}>();
const emit = defineEmits<{
  back: [];
  adjust: [];
  "update:pickerOpen": [value: boolean];
  toggleExample: [];
}>();
const pickerButton = ref<HTMLButtonElement | null>(null);
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) emit("update:pickerOpen", false);
  },
);
const closePicker = async () => {
  emit("update:pickerOpen", false);
  await nextTick();
  pickerButton.value?.focus({ preventScroll: true });
};
</script>

<template>
  <section class="panel-section live-plan-selection">
    <header class="live-plan-heading">
      <button
        class="icon-button"
        type="button"
        title="返回种植方式"
        :disabled="disabled"
        @click="$emit('back')"
      >
        <ArrowLeft :size="16" /></button
      ><strong>预设方案</strong>
    </header>
    <button
      ref="pickerButton"
      class="live-plan-open-picker"
      type="button"
      :disabled="disabled || controller.loadingProfileParameters.value"
      :aria-expanded="pickerOpen"
      @click="emit('update:pickerOpen', true)"
    >
      <Sprout :size="19" /><span
        ><small>种植方案</small
        ><strong>{{
          controller.selectedProfile.value?.display_name || "选择种植方案"
        }}</strong></span
      ><span class="live-plan-change">{{
        controller.selectedProfile.value ? "更换" : "选择"
      }}</span
      ><ChevronRight :size="16" />
    </button>
    <template v-if="controller.selectedProfile.value">
      <div class="live-plan-plants">
        <span
          v-for="(plant, index) in (
            controller.selectedProfile.value.plants ?? []
          ).slice(0, 4)"
          :key="`${plant.asset_id}:${index}`"
          :title="plant.display_name"
          ><VegetationThumbnail
            :src="controller.resourceThumbnail(plant.thumbnail_resource_id)"
        /></span>
      </div>
      <div class="live-plan-summary">
        <strong
          >{{ controller.selectedStrategy.value?.summary.display_name }}
          <small
            >v{{
              controller.selectedStrategy.value?.summary.strategy_version
            }}</small
          ></strong
        ><span>{{ controller.parameterSummary.value }}</span>
      </div>
      <div class="live-plan-actions">
        <button
          class="advanced-action"
          type="button"
          :disabled="disabled || controller.loadingProfileParameters.value"
          @click="$emit('adjust')"
        >
          <Pencil :size="15" />调整方案
        </button>
        <button
          v-if="hasExample"
          class="example-trigger"
          type="button"
          :aria-expanded="exampleOpen"
          aria-controls="live-parameter-example"
          @click="emit('toggleExample')"
        >
          <ChartScatter :size="14" />效果示意
        </button>
      </div>
    </template>
    <p v-if="!controller.availablePlans.value.length" class="live-plan-empty">
      暂无可用方案
    </p>
  </section>
  <PlantingPlanPicker
    v-if="pickerOpen"
    :controller="controller"
    :anchor="anchor"
    @close="closePicker"
  />
</template>
