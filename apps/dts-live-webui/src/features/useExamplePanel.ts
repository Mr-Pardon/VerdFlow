import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { Ref } from "vue";
import type {
  ExampleAppearanceRole,
  StrategyExampleCache,
} from "@verdflow/strategy-preview-ui";
import { strategyPreviewClient, closeStrategyExamples } from "../api";
import type { LiveConfiguration } from "./useLiveConfiguration";

export function useExamplePanel(
  configuration: LiveConfiguration,
  context: {
    panelCollapsed: Ref<boolean>;
    operationActive: Ref<boolean>;
    entryMode: Ref<string>;
  },
) {
  const open = ref(false);
  const planPickerOpen = ref(false);
  const cache: StrategyExampleCache = { signature: "", result: null };
  const available = computed(() =>
    Boolean(configuration.selectedStrategy.value?.parameter_preview),
  );
  const blocked = computed(
    () =>
      context.panelCollapsed.value ||
      context.operationActive.value ||
      context.entryMode.value === "choose" ||
      configuration.mappingOpen.value ||
      !available.value,
  );
  const source = computed(() =>
    configuration.selectedProfile.value
      ? (configuration.selectedProfileDetail.value?.assignments ?? {})
      : configuration.assignments.value,
  );
  const enabled = computed(() =>
    Object.fromEntries(
      Object.entries(source.value).map(([key, value]) => [
        key,
        value.enabled !== false,
      ]),
    ),
  );
  const appearance = computed<ExampleAppearanceRole[]>(() =>
    Object.entries(source.value).map(([role_key, assignment]) => ({
      role_key,
      candidates: assignment.candidates.map((candidate) => ({
        asset_id: candidate.asset_id,
        weight: candidate.weight,
        scale: candidate.scale,
        reference_radius_m:
          typeof candidate.reference_radius_m === "number" &&
          Number.isFinite(candidate.reference_radius_m) &&
          candidate.reference_radius_m > 0
            ? candidate.reference_radius_m
            : null,
      })),
    })),
  );
  const close = () => {
    open.value = false;
  };
  const toggle = () => {
    if (!blocked.value) open.value = !open.value;
  };
  watch(open, (value) => {
    if (value) planPickerOpen.value = false;
  });
  watch(planPickerOpen, (value) => {
    if (value) close();
  });
  watch(blocked, (value) => {
    if (value) {
      close();
      planPickerOpen.value = false;
    }
  });
  const pageHide = () => {
    close();
    void closeStrategyExamples().catch(() => {});
  };
  onMounted(() => window.addEventListener("pagehide", pageHide));
  onBeforeUnmount(() => {
    window.removeEventListener("pagehide", pageHide);
    pageHide();
    cache.result = null;
    cache.signature = "";
  });
  return {
    open,
    planPickerOpen,
    cache,
    available,
    appearance,
    enabled,
    close,
    toggle,
    client: strategyPreviewClient,
  };
}
export type ExamplePanel = ReturnType<typeof useExamplePanel>;
