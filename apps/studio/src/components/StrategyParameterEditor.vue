<script setup lang="ts">
import { RotateCcw, Shuffle, SlidersHorizontal } from "@lucide/vue";
import { computed } from "vue";

import type { StrategyGenerationParameterInfo } from "../api";

const props = withDefaults(
  defineProps<{
    parameters: StrategyGenerationParameterInfo[];
    values: Record<string, number>;
    randomSeed?: number | null;
    showSeed?: boolean;
    disabled?: boolean;
    simplified?: boolean;
    seedInAdvancedOnly?: boolean;
  }>(),
  { showSeed: true },
);

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

const currentValue = (parameter: StrategyGenerationParameterInfo) =>
  props.values[parameter.key] ?? parameter.default;

const updateParameter = (
  parameter: StrategyGenerationParameterInfo,
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
  <div class="strategy-parameter-editor">
    <div v-if="quickParameters.length" class="strategy-parameter-grid">
      <label
        v-for="parameter in quickParameters"
        :key="parameter.key"
        class="strategy-parameter-control"
      >
        <span class="strategy-parameter-heading">
          <strong>{{ parameter.display_name }}</strong>
          <input
            :value="currentValue(parameter)"
            type="number"
            :min="parameter.minimum"
            :max="parameter.maximum"
            :step="parameter.step"
            :disabled="disabled"
            @change="updateParameter(parameter, $event)"
          />
        </span>
        <span class="strategy-parameter-effect">{{ parameter.effect }}</span>
        <input
          class="strategy-parameter-range"
          :value="currentValue(parameter)"
          type="range"
          :min="parameter.minimum"
          :max="parameter.maximum"
          :step="parameter.step"
          :disabled="disabled"
          @input="updateParameter(parameter, $event)"
        />
        <span class="strategy-parameter-bounds">
          <small>{{ parameter.minimum }}</small>
          <small>{{ parameter.maximum }}</small>
        </span>
      </label>
    </div>

    <div v-else-if="!parameters.length" class="strategy-parameter-empty">
      <SlidersHorizontal :size="20" />
      <div>
        <strong>当前没有可调整的构图参数</strong>
      </div>
    </div>

    <section
      v-if="showSeed !== false && !seedInAdvancedOnly"
      class="strategy-seed-control"
    >
      <div>
        <strong>随机构图</strong>
        <span>相同区域、参数、植被启用状态和种子会生成相同构图。</span>
      </div>
      <label v-if="!simplified">
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
        class="secondary-button"
        type="button"
        :disabled="disabled"
        title="生成新的随机种子"
        @click="emit('reroll')"
      >
        <Shuffle :size="15" />
        换一组
      </button>
    </section>

    <details
      v-if="advancedParameters.length || (simplified && showSeed !== false)"
      class="strategy-advanced-settings"
    >
      <summary>
        <span><SlidersHorizontal :size="16" />高级设置</span>
        <small
          >{{
            advancedParameters.length +
            (simplified && showSeed !== false ? 1 : 0)
          }}
          项</small
        >
      </summary>
      <div class="strategy-parameter-grid">
        <label
          v-if="simplified && showSeed !== false"
          class="strategy-parameter-control"
        >
          <span class="strategy-parameter-heading">
            <strong>随机种子</strong>
            <input
              :value="randomSeed ?? ''"
              type="number"
              step="1"
              :disabled="disabled"
              @change="updateSeed"
            />
          </span>
          <span class="strategy-parameter-effect"
            >相同区域、参数、植被启用状态和种子会生成相同构图。</span
          >
          <button
            v-if="seedInAdvancedOnly"
            class="strategy-parameter-reset"
            type="button"
            :disabled="disabled"
            title="更换实际种植的随机构图"
            @click="emit('reroll')"
          >
            <Shuffle :size="14" />更换种植种子
          </button>
        </label>
        <label
          v-for="parameter in advancedParameters"
          :key="parameter.key"
          class="strategy-parameter-control"
        >
          <span class="strategy-parameter-heading">
            <strong>{{ parameter.display_name }}</strong>
            <input
              :value="currentValue(parameter)"
              type="number"
              :min="parameter.minimum"
              :max="parameter.maximum"
              :step="parameter.step"
              :disabled="disabled"
              @change="updateParameter(parameter, $event)"
            />
          </span>
          <span class="strategy-parameter-effect">{{ parameter.effect }}</span>
          <input
            class="strategy-parameter-range"
            :value="currentValue(parameter)"
            type="range"
            :min="parameter.minimum"
            :max="parameter.maximum"
            :step="parameter.step"
            :disabled="disabled"
            @input="updateParameter(parameter, $event)"
          />
          <span class="strategy-parameter-bounds">
            <small>{{ parameter.minimum }}</small>
            <small>{{ parameter.maximum }}</small>
          </span>
        </label>
      </div>
    </details>

    <button
      v-if="parameters.length"
      class="strategy-parameter-reset"
      type="button"
      :disabled="disabled"
      @click="emit('reset')"
    >
      <RotateCcw :size="14" />
      恢复策略默认值
    </button>
  </div>
</template>
