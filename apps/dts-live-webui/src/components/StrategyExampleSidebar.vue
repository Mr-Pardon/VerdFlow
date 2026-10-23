<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";
import { X } from "@lucide/vue";
import { StrategyExamplePreview } from "@verdflow/strategy-preview-ui";
import type { LiveConfiguration } from "../features/useLiveConfiguration";
import type { ExamplePanel } from "../features/useExamplePanel";
import { useAdjacentPanel } from "../features/useAdjacentPanel";
import { scrollPanelWheel } from "../shared/panelScroll";

const props = defineProps<{
  controller: ExamplePanel;
  configuration: LiveConfiguration;
  anchor: HTMLElement | null;
}>();
const { position, overlay } = useAdjacentPanel(() => props.anchor, {
  width: 460,
});
const escape = (event: KeyboardEvent) => {
  if (event.key === "Escape") {
    event.stopPropagation();
    props.controller.close();
  }
};
onMounted(() => {
  document.addEventListener("keydown", escape, true);
});
onBeforeUnmount(() => {
  document.removeEventListener("keydown", escape, true);
});
</script>

<template>
  <Teleport to="body">
    <aside
      id="live-parameter-example"
      class="live-example-sidebar"
      :class="{ overlay }"
      :style="position"
      role="dialog"
      aria-label="参数效果示意"
      aria-modal="false"
      @pointerdown.stop
      @pointermove.stop
      @pointerup.stop
      @mousedown.stop
      @mousemove.stop
      @mouseup.stop
      @click.stop
      @dblclick.stop
      @contextmenu.stop
      @wheel="scrollPanelWheel"
    >
      <StrategyExamplePreview
        v-if="configuration.selectedStrategy.value"
        theme="dark"
        :client="controller.client"
        :cache="controller.cache"
        :strategy="configuration.selectedStrategy.value"
        :values="configuration.strategyParameterValues.value"
        :enabled="controller.enabled.value"
        :appearance="controller.appearance.value"
        :paused="configuration.loadingProfileParameters.value"
      >
        <template #header-actions
          ><button
            class="icon-button"
            type="button"
            title="关闭效果示意"
            @click="controller.close"
          >
            <X :size="16" /></button
        ></template>
      </StrategyExamplePreview>
    </aside>
  </Teleport>
</template>
