<script setup lang="ts">
import {
  RotateCcw,
  Shuffle,
  SlidersHorizontal,
} from "@lucide/vue";
import { computed } from "vue";

import type { StrategyGenerationParameter } from "../types";

const props = defineProps<{
  parameters: StrategyGenerationParameter[];
  values: Record<string, number>;
  randomSeed: number | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  updateParameter: [key: string, value: number];
  updateRandomSeed: [value: number];
  reset: [];
  reroll: [];
}>();

const quickParameters = computed(() =>
  props.parameters.filter((parameter) => parameter.group === "quick"),
);
const advancedParameters = computed(() =>
  props.parameters.filter((parameter) => parameter.group === "advanced"),
);

const currentValue = (parameter: StrategyGenerationParameter) =>
  props.values[parameter.key] ?? parameter.default;

const updateParameter = (
  parameter: StrategyGenerationParameter,
  event: Event,
) => {
  const value = Number((event.target as HTMLInputElement).value);
  if (Number.isFinite(value)) emit("updateParameter", parameter.key, value);
};

const updateSeed = (event: Event) => {
  const value = Number((event.target as HTMLInputElement).value);
  if (Number.isSafeInteger(value)) emit("updateRandomSeed", value);
};
</script>

<template>
  <div class="live-parameter-editor">
    <div v-if="quickParameters.length" class="parameter-list">
      <label
        v-for="parameter in quickParameters"
        :key="parameter.key"
        class="parameter-control"
      >
        <span class="parameter-heading">
          <span class="parameter-name">
            <SlidersHorizontal :size="16" />
            <strong>{{ parameter.display_name }}</strong>
          </span>
          <input
            :value="currentValue(parameter)"
            type="number"
            :min="parameter.minimum"
            :max="parameter.maximum"
            :step="parameter.step"
            :disabled="disabled"
            @input="updateParameter(parameter, $event)"
          />
        </span>
        <span class="parameter-effect" :title="parameter.effect">
          {{ parameter.effect }}
        </span>
        <input
          class="parameter-range"
          :value="currentValue(parameter)"
          type="range"
          :min="parameter.minimum"
          :max="parameter.maximum"
          :step="parameter.step"
          :disabled="disabled"
          @input="updateParameter(parameter, $event)"
        />
      </label>
    </div>

    <div v-else-if="!parameters.length" class="parameter-empty">
      <SlidersHorizontal :size="17" />
      <span>当前没有可调整的构图参数</span>
    </div>

    <details v-if="advancedParameters.length" class="advanced-parameters">
      <summary>
        <span><SlidersHorizontal :size="15" />高级参数</span>
        <small>{{ advancedParameters.length }} 项</small>
      </summary>
      <div class="parameter-list">
        <label
          v-for="parameter in advancedParameters"
          :key="parameter.key"
          class="parameter-control"
        >
          <span class="parameter-heading">
            <span class="parameter-name">
              <SlidersHorizontal :size="16" />
              <strong>{{ parameter.display_name }}</strong>
            </span>
            <input
              :value="currentValue(parameter)"
              type="number"
              :min="parameter.minimum"
              :max="parameter.maximum"
              :step="parameter.step"
              :disabled="disabled"
              @input="updateParameter(parameter, $event)"
            />
          </span>
          <span class="parameter-effect" :title="parameter.effect">
            {{ parameter.effect }}
          </span>
          <input
            class="parameter-range"
            :value="currentValue(parameter)"
            type="range"
            :min="parameter.minimum"
            :max="parameter.maximum"
            :step="parameter.step"
            :disabled="disabled"
            @input="updateParameter(parameter, $event)"
          />
        </label>
      </div>
    </details>

    <div class="seed-control">
      <label>
        <span>随机种子</span>
        <input
          :value="randomSeed ?? ''"
          type="number"
          step="1"
          :disabled="disabled"
          @change="updateSeed"
        />
      </label>
      <button
        class="icon-button"
        type="button"
        title="换一个随机种子"
        :disabled="disabled"
        @click="emit('reroll')"
      >
        <Shuffle :size="15" />
      </button>
    </div>

    <button
      v-if="parameters.length"
      class="parameter-reset"
      type="button"
      :disabled="disabled"
      @click="emit('reset')"
    >
      <RotateCcw :size="14" />恢复策略默认值
    </button>
  </div>
</template>
