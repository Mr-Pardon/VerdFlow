<script setup lang="ts">
import { CircleAlert, LoaderCircle, RefreshCw } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type {
  PreviewStrategy,
  StrategyPreviewInput,
  StrategyPreviewClient,
} from "./types";
import { useStrategyExample } from "./useStrategyExample";
import { drawStrategyExample } from "./strategyExampleCanvas";
import type { ExampleAppearanceRole } from "./types";
import type { StrategyExampleCache } from "./useStrategyExample";
import { useExampleReferenceRanges } from "./useExampleReferenceRanges";
import {
  exampleRoleColor,
  strategyExamplePalette,
  type StrategyExampleTheme,
} from "./strategyExampleTheme";

const props = defineProps<{
  strategy: PreviewStrategy;
  client: StrategyPreviewClient;
  values: Record<string, number>;
  enabled: Record<string, boolean>;
  paused?: boolean;
  appearance?: ExampleAppearanceRole[];
  cache?: StrategyExampleCache;
  theme?: StrategyExampleTheme;
}>();
const palette = computed(() => strategyExamplePalette(props.theme));
const canvas = ref<HTMLCanvasElement | null>(null);
const solidPoints = ref(false);
const hiddenRoleKeys = ref<string[]>([]);
const toggleRoleVisibility = (key: string) => {
  hiddenRoleKeys.value = hiddenRoleKeys.value.includes(key)
    ? hiddenRoleKeys.value.filter((item) => item !== key)
    : [...hiddenRoleKeys.value, key];
};
const request = computed<StrategyPreviewInput | null>(() => {
  if (
    !props.strategy.parameter_preview ||
    !props.strategy.summary.executable ||
    props.paused
  )
    return null;
  if (
    props.strategy.generation_parameters.some((field) => {
      const value = props.values[field.key] ?? field.default;
      return (
        !Number.isFinite(value) ||
        value < field.minimum ||
        value > field.maximum
      );
    })
  )
    return null;
  const excluded = props.strategy.roles
    .filter((role) => props.enabled[role.role_key] === false)
    .map((role) => role.role_key);
  if (excluded.length === props.strategy.roles.length) return null;
  return {
    package_hash: props.strategy.summary.package_hash,
    strategy_parameters: Object.fromEntries(
      props.strategy.generation_parameters.map((field) => [
        field.key,
        props.values[field.key] ?? field.default,
      ]),
    ),
    excluded_role_keys: excluded,
  };
});
const preview = useStrategyExample(
  () => request.value,
  props.cache,
  props.client,
);
const referenceRadii = useExampleReferenceRanges(
  () => preview.result.value,
  () => props.appearance ?? [],
  () => props.strategy.parameter_preview?.fixed_seed ?? 17,
  props.client,
);
const hasRanges = computed(() =>
  referenceRadii.value.some((radius) => radius !== null),
);
const legend = computed(() => {
  const result = preview.result.value;
  const used = new Set(
    result?.points.map((point) => result.role_keys[point[2]]) ?? [],
  );
  return props.strategy.roles.filter(
    (role) =>
      props.enabled[role.role_key] !== false &&
      (!result || used.has(role.role_key)),
  );
});
const allHidden = computed(
  () =>
    legend.value.length > 0 &&
    legend.value.every((role) => hiddenRoleKeys.value.includes(role.role_key)),
);
let frame: number | null = null;
let observer: ResizeObserver | null = null;
let disposed = false;
const render = () => {
  if (disposed || frame !== null) return;
  frame = requestAnimationFrame(() => {
    frame = null;
    if (canvas.value && props.strategy.parameter_preview)
      drawStrategyExample(
        canvas.value,
        props.strategy.parameter_preview,
        preview.result.value,
        props.strategy.roles,
        referenceRadii.value,
        {
          hiddenRoleKeys: hiddenRoleKeys.value,
          solidPoints: solidPoints.value,
          theme: props.theme,
        },
      );
  });
};
watch(
  () => [
    preview.result.value,
    props.strategy.parameter_preview,
    referenceRadii.value,
    hiddenRoleKeys.value,
    solidPoints.value,
    props.theme,
  ],
  render,
);
onMounted(() => {
  observer = new ResizeObserver(render);
  if (canvas.value) observer.observe(canvas.value);
  render();
});
onBeforeUnmount(() => {
  disposed = true;
  observer?.disconnect();
  if (frame !== null) cancelAnimationFrame(frame);
});
</script>

<template>
  <figure
    class="strategy-example-preview"
    :aria-busy="preview.updating.value"
    :style="{
      '--example-canvas-background': palette.background,
      '--example-message-background': palette.overlay,
      '--example-switch-background': palette.switchTrack,
    }"
  >
    <header>
      <div>
        <span class="strategy-example-eyebrow">效果示意</span
        ><strong>{{ strategy.parameter_preview?.display_name }}</strong>
      </div>
      <div class="strategy-example-actions">
        <span
          v-if="preview.updating.value"
          class="strategy-example-state"
          role="status"
          ><LoaderCircle :size="13" class="spinning" />更新中</span
        >
        <button
          v-else-if="preview.error.value"
          class="strategy-example-retry"
          type="button"
          :title="preview.error.value"
          @click="preview.refresh"
        >
          <RefreshCw :size="13" />重试
        </button>
        <label class="strategy-example-switch">
          <span>实心点位</span>
          <input
            v-model="solidPoints"
            type="checkbox"
            role="switch"
            aria-label="实心点位"
          />
          <span class="strategy-example-switch-track" aria-hidden="true"
            ><span
          /></span>
        </label>
        <slot name="header-actions" />
      </div>
    </header>
    <div class="strategy-example-canvas">
      <canvas ref="canvas" aria-label="当前参数的示例种植分布" />
      <span
        v-if="preview.error.value && !preview.result.value"
        class="strategy-example-message"
        ><CircleAlert :size="17" />示例暂不可用</span
      >
      <span
        v-else-if="preview.result.value && !preview.result.value.points.length"
        class="strategy-example-message"
        >当前配置未生成点位</span
      >
    </div>
    <figcaption>
      <small
        v-if="hasRanges && !solidPoints"
        class="reference-range-label"
        title="按植被参考半径与缩放显示，不代表实际冠幅或碰撞边界"
        >参考范围</small
      >
      <button
        v-for="role in legend"
        :key="role.role_key"
        class="strategy-example-legend-item"
        :class="{ hidden: hiddenRoleKeys.includes(role.role_key) }"
        type="button"
        :aria-pressed="!hiddenRoleKeys.includes(role.role_key)"
        :title="`${hiddenRoleKeys.includes(role.role_key) ? '显示' : '隐藏'}${role.display_name}`"
        @click="toggleRoleVisibility(role.role_key)"
      >
        <i
          :style="{
            background: exampleRoleColor(strategy.roles, role.role_key, theme),
          }"
        />{{ role.display_name }}
      </button>
      <small v-if="allHidden" role="status">点位已全部隐藏</small>
      <small v-if="!request && !paused">请检查当前参数</small>
    </figcaption>
  </figure>
</template>

<style scoped>
.strategy-example-preview {
  margin: 0;
  min-width: 0;
  position: sticky;
  top: 0;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--color-surface);
}
.strategy-example-preview > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 15px 18px;
  border-bottom: 1px solid var(--color-border);
  min-height: 67px;
}
.strategy-example-preview > header > div:first-child {
  min-width: 0;
  overflow-wrap: anywhere;
}
.strategy-example-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 6px 12px;
  flex-shrink: 0;
}
.strategy-example-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
  color: var(--color-text-muted);
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}
.strategy-example-switch input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}
.strategy-example-switch-track {
  display: inline-flex;
  align-items: center;
  width: 34px;
  height: 20px;
  padding: 2px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--example-switch-background);
  transition:
    background-color 140ms ease-out,
    border-color 140ms ease-out;
}
.strategy-example-switch-track > span {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px #18392b24;
  transition: transform 140ms ease-out;
}
.strategy-example-switch input:checked + .strategy-example-switch-track {
  border-color: var(--color-accent);
  background: var(--color-accent);
}
.strategy-example-switch input:checked + .strategy-example-switch-track > span {
  transform: translateX(14px);
}
.strategy-example-switch input:focus-visible + .strategy-example-switch-track {
  outline: 2px solid var(--color-accent);
  outline-offset: 3px;
}
.strategy-example-eyebrow {
  display: block;
  font-size: 10px;
  color: var(--color-text-faint);
  margin-bottom: 4px;
}
.strategy-example-preview strong {
  font-size: 13px;
  font-weight: 600;
}
.strategy-example-state,
.strategy-example-retry {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--color-text-muted);
  white-space: nowrap;
}
.strategy-example-retry {
  border: 0;
  background: transparent;
  cursor: pointer;
  padding: 5px;
  color: var(--color-accent);
}
.strategy-example-canvas {
  position: relative;
  width: 100%;
  aspect-ratio: 1.55;
  min-height: 200px;
  max-height: 340px;
  overflow: hidden;
  background: var(--example-canvas-background);
}
.strategy-example-canvas canvas {
  display: block;
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.strategy-example-message {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-text-muted);
  font-size: 12px;
  background: var(--example-message-background);
}
figcaption {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 15px;
  padding: 12px 18px;
  min-height: 43px;
  font-size: 11px;
  color: var(--color-text-muted);
  border-top: 1px solid var(--color-border);
}
.strategy-example-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
  border: 0;
  border-radius: 2px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.strategy-example-legend-item.hidden {
  opacity: 0.42;
  text-decoration: line-through;
}
.strategy-example-legend-item:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 4px;
}
figcaption i {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 50%;
}
figcaption small {
  font: inherit;
}
.reference-range-label {
  color: var(--color-text-faint);
}
@media (prefers-reduced-motion: reduce) {
  .strategy-example-switch-track,
  .strategy-example-switch-track > span {
    transition: none;
  }
}
@media (max-width: 760px) {
  .strategy-example-preview {
    position: static;
  }
  .strategy-example-canvas {
    aspect-ratio: 1.35;
    min-height: 250px;
  }
}
@media (min-width: 761px) and (max-height: 900px) {
  .strategy-example-canvas {
    max-height: 260px;
  }
}
</style>
