<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

import { libraryResourceUrl } from "./libraryResourceUrls";

const props = defineProps<{
  resourceId: string | null;
  alt?: string;
}>();

const url = ref<string | null>(null);
const failed = ref(false);
let requestRevision = 0;

watch(
  () => props.resourceId,
  async (resourceId) => {
    const revision = ++requestRevision;
    url.value = null;
    failed.value = false;
    if (!resourceId) return;
    try {
      const resolved = await libraryResourceUrl(resourceId);
      if (revision === requestRevision) url.value = resolved;
    } catch {
      if (revision === requestRevision) url.value = null;
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  requestRevision += 1;
});
</script>

<template>
  <img v-if="url && !failed" :src="url" :alt="alt ?? ''" @error="failed = true" />
  <slot v-else />
</template>
