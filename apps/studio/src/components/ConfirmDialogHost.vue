<script setup lang="ts">
import { AlertTriangle, CircleHelp } from "@lucide/vue";
import { nextTick, ref, watch } from "vue";

import {
  activeConfirmation,
  settleConfirmation,
} from "./confirmDialog";

const cancelButton = ref<HTMLButtonElement | null>(null);

watch(activeConfirmation, async (confirmation) => {
  if (confirmation === null) return;
  await nextTick();
  cancelButton.value?.focus();
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="activeConfirmation"
      class="confirm-dialog-backdrop"
      @click.self="settleConfirmation(false)"
      @keydown.esc="settleConfirmation(false)"
    >
      <section
        class="confirm-dialog"
        :data-tone="activeConfirmation.tone ?? 'default'"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="global-confirm-title"
        aria-describedby="global-confirm-message"
      >
        <div class="confirm-dialog__icon" aria-hidden="true">
          <AlertTriangle
            v-if="activeConfirmation.tone === 'danger'"
            :size="21"
          />
          <CircleHelp v-else :size="21" />
        </div>
        <div class="confirm-dialog__content">
          <h2 id="global-confirm-title">{{ activeConfirmation.title }}</h2>
          <p id="global-confirm-message">{{ activeConfirmation.message }}</p>
          <code v-if="activeConfirmation.detail">{{ activeConfirmation.detail }}</code>
        </div>
        <footer>
          <button
            ref="cancelButton"
            class="secondary-button"
            type="button"
            @click="settleConfirmation(false)"
          >
            {{ activeConfirmation.cancelLabel ?? "取消" }}
          </button>
          <button
            :class="activeConfirmation.tone === 'danger' ? 'danger-button' : 'primary-button'"
            type="button"
            @click="settleConfirmation(true)"
          >
            {{ activeConfirmation.confirmLabel ?? "确认" }}
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
