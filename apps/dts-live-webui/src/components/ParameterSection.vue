<script setup lang="ts">
import {
  ChevronUp,
  ChevronDown,
  SlidersHorizontal,
  ChartScatter,
} from "@lucide/vue";
import StrategyParameterPanel from "./StrategyParameterPanel.vue";
import type { LiveConfiguration } from "../features/useLiveConfiguration";
const props = defineProps<{
  controller: LiveConfiguration;
  selectionLocked: boolean;
  hasExample: boolean;
  exampleOpen: boolean;
}>();
const emit = defineEmits<{ toggleExample: [] }>();
const {
  loadingProfileParameters,
  selectedStrategy,
  visibleGenerationParameters,
  parameterSummary,
  strategyParameterValues,
  randomSeed,
  updateStrategyParameter,
  updateRandomSeed,
  rerollRandomSeed,
  resetStrategyParameters,
} = props.controller;
import { ref } from "vue";
const parameterSectionExpanded = ref(true);
</script>

<template>
  <section v-if="selectedStrategy" class="panel-section parameter-section">
    <div class="parameter-section-heading">
      <button
        class="section-heading section-toggle"
        type="button"
        :aria-expanded="parameterSectionExpanded"
        @click="parameterSectionExpanded = !parameterSectionExpanded"
      >
        <div>
          <span class="section-index">03</span>
          <strong>构图参数</strong>
        </div>
        <span class="section-heading-actions">
          <span v-if="selectionLocked" class="locked-label">本次已锁定</span>
          <ChevronUp v-if="parameterSectionExpanded" :size="16" />
          <ChevronDown v-else :size="16" />
        </span>
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
    <div v-if="!parameterSectionExpanded" class="section-summary">
      <SlidersHorizontal :size="16" />
      <span>{{ parameterSummary }}</span>
    </div>
    <StrategyParameterPanel
      v-else
      :parameters="visibleGenerationParameters"
      :values="strategyParameterValues"
      :random-seed="randomSeed"
      :disabled="selectionLocked || loadingProfileParameters"
      @update-parameter="updateStrategyParameter"
      @update-random-seed="updateRandomSeed"
      @reset="resetStrategyParameters"
      @reroll="rerollRandomSeed"
    />
  </section>
</template>
