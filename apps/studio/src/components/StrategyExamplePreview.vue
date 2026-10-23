<script setup lang="ts">
import { StrategyExamplePreview } from "@verdflow/strategy-preview-ui";
import type {
  ExampleAppearanceRole,
  StrategyExampleCache,
} from "@verdflow/strategy-preview-ui";
import type { StrategyLibraryDetail } from "../api";
import { LocalApiClient } from "../api";
import { platformBridge } from "../platform";
defineProps<{
  strategy: StrategyLibraryDetail;
  values: Record<string, number>;
  enabled: Record<string, boolean>;
  paused?: boolean;
  appearance?: ExampleAppearanceRole[];
  cache?: StrategyExampleCache;
}>();
const client = new LocalApiClient(platformBridge());
</script>
<template>
  <StrategyExamplePreview
    :client="client"
    :strategy="strategy"
    :values="values"
    :enabled="enabled"
    :paused="paused"
    :appearance="appearance"
    :cache="cache"
  />
</template>
