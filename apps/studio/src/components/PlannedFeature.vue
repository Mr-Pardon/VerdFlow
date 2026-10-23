<script setup lang="ts">
import { Box } from "@lucide/vue";

defineProps<{ title: string; description: string; note?: string }>();
</script>

<template>
  <section class="planned-feature" aria-label="功能规划">
    <Box :size="36" :stroke-width="1.4" aria-hidden="true" />
    <span class="planned-feature__status">规划中</span>
    <h2>{{ title }}</h2>
    <p>{{ description }}</p>
    <p v-if="note" class="planned-feature__note">{{ note }}</p>
    <div class="planned-feature__actions"><slot /></div>
  </section>
</template>

<style scoped>
.planned-feature { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 340px; padding: 48px 24px; text-align: center; color: var(--color-text-muted); }
.planned-feature > svg { color: #78978c; margin-bottom: 18px; }
.planned-feature__status { font-size: 12px; color: #6d8279; padding: 3px 9px; background: #eaf0ed; border-radius: 4px; }
.planned-feature h2 { margin: 16px 0 10px; font-size: 22px; line-height: 1.5; color: var(--color-text); }
.planned-feature p { max-width: 460px; margin: 0; font-size: 14px; line-height: 1.9; overflow-wrap: anywhere; }
.planned-feature .planned-feature__note { margin-top: 8px; font-size: 13px; color: var(--color-text-faint); }
.planned-feature__actions { margin-top: 26px; }
@media (max-width: 600px) { .planned-feature { min-height: 280px; padding: 32px 16px; } .planned-feature h2 { font-size: 20px; } }
</style>
