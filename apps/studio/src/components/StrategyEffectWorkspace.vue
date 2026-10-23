<script setup lang="ts">
import type {
  StrategyGenerationParameterInfo,
  StrategyLibraryDetail,
} from "../api";
import StrategyExamplePreview from "./StrategyExamplePreview.vue";
import StrategyParameterEditor from "./StrategyParameterEditor.vue";
import type { ExampleAppearanceRole } from "../features/shared/strategyExampleAppearance";
import type { StrategyExampleCache } from "@verdflow/strategy-preview-ui";

defineProps<{
  strategy: StrategyLibraryDetail;
  parameters: StrategyGenerationParameterInfo[];
  values: Record<string, number>;
  enabled: Record<string, boolean>;
  randomSeed: number | null;
  disabled?: boolean;
  appearance?: ExampleAppearanceRole[];
  exampleCache?: StrategyExampleCache;
}>();
const emit = defineEmits<{
  updateParameter: [key: string, value: number];
  updateRandomSeed: [value: number];
  reset: [];
  reroll: [];
}>();
</script>

<template>
  <div
    class="strategy-effect-workspace"
    :class="{ 'has-example': strategy.parameter_preview }"
  >
    <div class="strategy-effect-controls">
      <StrategyParameterEditor
        simplified
        seed-in-advanced-only
        :parameters="parameters"
        :values="values"
        :random-seed="randomSeed"
        :disabled="disabled"
        @update-parameter="(key, value) => emit('updateParameter', key, value)"
        @update-random-seed="emit('updateRandomSeed', $event)"
        @reroll="emit('reroll')"
        @reset="emit('reset')"
      />
    </div>
    <StrategyExamplePreview
      v-if="strategy.parameter_preview"
      :key="strategy.summary.package_hash"
      :strategy="strategy"
      :values="values"
      :enabled="enabled"
      :paused="disabled"
      :appearance="appearance"
      :cache="exampleCache"
    />
  </div>
</template>

<style scoped>
.strategy-effect-workspace {
  display: block;
  min-width: 0;
}
.strategy-effect-workspace.has-example {
  display: grid;
  grid-template-columns: minmax(235px, 0.85fr) minmax(280px, 1.15fr);
  gap: 26px;
  align-items: start;
}
.strategy-effect-controls {
  min-width: 0;
}
.has-example :deep(.strategy-parameter-grid) {
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}
.strategy-effect-controls :deep(.strategy-parameter-control) {
  padding: 14px 16px 12px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface-soft);
}
.strategy-effect-controls :deep(.strategy-parameter-control:focus-within) {
  border-color: var(--color-accent);
}
.has-example :deep(.strategy-parameter-effect) {
  font-size: 11px;
  line-height: 1.65;
}
.has-example :deep(.strategy-parameter-heading strong) {
  font-size: 12px;
}
.has-example :deep(.strategy-parameter-heading input) {
  width: 76px;
  min-height: 32px;
}
.has-example :deep(.strategy-parameter-bounds) {
  font-size: 10px;
}
.has-example :deep(.strategy-advanced-settings) {
  margin-top: 22px;
  border: 0;
  border-top: 1px solid var(--color-border);
  border-radius: 0;
}
.has-example :deep(.strategy-advanced-settings > .strategy-parameter-grid) {
  padding: 0;
}
@media (max-width: 1100px) {
  .strategy-effect-workspace.has-example {
    grid-template-columns: minmax(220px, 0.9fr) minmax(250px, 1.1fr);
    gap: 18px;
  }
}
@media (max-width: 760px) {
  .strategy-effect-workspace.has-example {
    display: flex;
    flex-direction: column;
    gap: 22px;
  }
  .strategy-effect-workspace.has-example > * {
    width: 100%;
  }
  .strategy-example-preview {
    order: -1;
  }
}
</style>
