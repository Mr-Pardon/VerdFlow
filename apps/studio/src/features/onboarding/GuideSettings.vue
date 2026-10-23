<script setup lang="ts">
import { inject } from "vue";
import { ArrowRight, Compass } from "@lucide/vue";
import { firstUseGuideKey } from "./context";
const guide = inject(firstUseGuideKey, null);
</script>

<template>
  <section v-if="guide" class="guide-settings" aria-label="产品引导">
    <span class="guide-settings__icon" aria-hidden="true"><Compass :size="20" /></span>
    <div class="guide-settings__copy">
      <h2>产品引导</h2>
      <p>认识主要入口，按需准备资源与运行环境。</p>
    </div>
    <button class="secondary-button guide-settings__action" type="button" :disabled="guide.busy.value" @click="guide.open">
      {{ guide.progress.value.status === 'paused' && guide.progress.value.visited.length ? '继续引导' : '查看引导' }}
      <ArrowRight :size="16" />
    </button>
  </section>
</template>
