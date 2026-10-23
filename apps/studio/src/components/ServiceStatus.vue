<script setup lang="ts">
import { computed } from "vue";

import type { ConnectionState } from "../features/projects/useProjects";

const props = defineProps<{
  state: ConnectionState;
}>();

const label = computed(() => {
  if (props.state === "connected") {
    return "服务正常";
  }
  if (props.state === "connecting") {
    return "服务连接中";
  }
  return "服务不可用";
});
</script>

<template>
  <div class="service-status" :data-state="state" role="status">
    <span class="service-status__dot" aria-hidden="true" />
    <span>{{ label }}</span>
  </div>
</template>
