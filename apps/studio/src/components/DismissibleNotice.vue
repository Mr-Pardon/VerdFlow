<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { X } from "@lucide/vue";

const props = defineProps<{ message: string | null | undefined; noticeKey?: string | number | null; status?: boolean }>();
const emit = defineEmits<{ dismiss: [] }>();
const dismissed = ref(false);
watch(() => [props.message, props.noticeKey], () => { dismissed.value = false; });
const visible = computed(() => Boolean(props.message) && !dismissed.value);
function dismiss() {
  dismissed.value = true;
  emit("dismiss");
}
</script>

<template>
  <div v-if="visible" class="dismissible-notice" :role="status ? 'status' : 'alert'">
    <div class="dismissible-notice__content"><slot>{{ message }}</slot></div>
    <div v-if="$slots.actions" class="dismissible-notice__actions"><slot name="actions" /></div>
    <button type="button" class="dismissible-notice__close" aria-label="关闭提示" title="关闭提示" @click="dismiss"><X :size="16" /></button>
  </div>
</template>

<style scoped>
.dismissible-notice { display: flex; align-items: flex-start; gap: 10px; max-width: 100%; }
.dismissible-notice__content { min-width: 0; flex: 1; overflow-wrap: anywhere; }
.dismissible-notice__content :deep(p) { margin: 0; }
.dismissible-notice__actions { display: flex; flex: 0 0 auto; }
.dismissible-notice .dismissible-notice__close { display: grid; place-items: center; flex: 0 0 28px; width: 28px; height: 28px; min-width: 28px; padding: 0; margin: -3px -3px 0 0; border: 0; border-radius: 4px; background: transparent; color: inherit; cursor: pointer; }
.dismissible-notice .dismissible-notice__close:hover { background: rgba(0, 0, 0, 0.07); }
.dismissible-notice .dismissible-notice__close:focus-visible { outline: 2px solid currentColor; outline-offset: 1px; }
</style>
