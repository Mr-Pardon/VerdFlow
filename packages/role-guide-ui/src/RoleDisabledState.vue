<script setup lang="ts">
import { Ban } from "@lucide/vue";

defineProps<{ name: string; candidateCount: number }>();
</script>

<template>
  <div class="role-disabled-state" role="status">
    <Ban :size="32" aria-hidden="true" />
    <strong>{{ name }}已禁用</strong>
    <p>本次种植不使用此用途的植被。</p>
    <small>{{
      candidateCount
        ? `已保留 ${candidateCount} 种植被的搭配，重新启用后可继续调整。`
        : "重新启用后可选择植被。"
    }}</small>
  </div>
</template>

<style scoped>
.role-disabled-state {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  text-align: center;
  color: var(--color-text-muted, #75877c);
}
.role-disabled-state > svg {
  flex-shrink: 0;
  margin-bottom: 18px;
  color: #89a99b;
}
.role-disabled-state strong {
  color: var(--color-text, #344b3f);
  font-size: 15px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.role-disabled-state p {
  margin: 10px 0 6px;
  font-size: 12px;
  line-height: 1.7;
}
.role-disabled-state small {
  font-size: 11px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
</style>
