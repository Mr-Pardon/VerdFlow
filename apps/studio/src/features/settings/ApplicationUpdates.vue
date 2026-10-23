<script setup lang="ts">
import { Download, RefreshCw, RotateCw, X } from "@lucide/vue";
import { inject } from "vue";
import { version } from "../../../../../package.json";
import { applicationUpdatesKey } from "./useApplicationUpdates";

const updates = inject(applicationUpdatesKey);
const preference = (key: "autoCheck" | "autoDownload", event: Event) => {
  const state = updates?.state.value;
  if (state) void updates?.preferences({ autoCheck: state.autoCheck, autoDownload: state.autoDownload, [key]: (event.target as HTMLInputElement).checked });
};
</script>

<template>
  <section v-if="updates" class="application-updates" aria-label="应用更新">
    <div class="application-updates__heading">
      <div><h2>应用更新 <small>v{{ updates.state.value?.currentVersion || version }}</small></h2><p v-if="updates.label.value" role="status">{{ updates.label.value }}</p></div>
      <div v-if="updates.state.value?.supported && updates.state.value.configured" class="application-updates__actions">
        <button v-if="updates.state.value.stage === 'ready'" class="primary-button" :disabled="updates.acting.value" @click="updates.run('apply')"><RotateCw :size="16" />重启并更新</button>
        <button v-else-if="['downloading', 'preparing'].includes(updates.state.value.stage)" class="secondary-button" @click="updates.run('cancel')"><X :size="16" />取消下载</button>
        <template v-else>
          <button v-if="updates.state.value.latestVersion" class="primary-button" :disabled="updates.acting.value" @click="updates.run('download')"><Download :size="16" />下载更新</button>
          <button class="secondary-button" :disabled="updates.acting.value || ['checking', 'applying'].includes(updates.state.value.stage)" @click="updates.run('check')"><RefreshCw :size="16" :class="{ spinning: updates.state.value.stage === 'checking' }" />检查更新</button>
        </template>
      </div>
    </div>
    <progress v-if="updates.state.value && ['downloading', 'preparing'].includes(updates.state.value.stage)" :value="updates.state.value.progress" max="100" aria-label="更新下载进度" />
    <p v-if="updates.error.value || (updates.state.value?.message && updates.state.value.message !== updates.label.value)"
      class="application-updates__message" :class="{ 'application-updates__message--error': updates.error.value || updates.state.value?.stage === 'error' }"
      :role="updates.error.value || updates.state.value?.stage === 'error' ? 'alert' : 'status'"
    >{{ updates.error.value || updates.state.value?.message }}</p>
    <div v-if="updates.state.value?.supported" class="application-updates__preferences">
      <label class="update-preference"><span>自动检查更新</span><input class="update-switch" type="checkbox" role="switch" :disabled="updates.savingPreferences.value" :checked="updates.state.value.autoCheck" @change="preference('autoCheck', $event)" /></label>
      <label class="update-preference"><span>发现更新后自动下载</span><input class="update-switch" type="checkbox" role="switch" :disabled="updates.savingPreferences.value" :checked="updates.state.value.autoDownload" @change="preference('autoDownload', $event)" /></label>
    </div>
  </section>
</template>

<style scoped>
.application-updates { padding: 22px; border-bottom: 1px solid var(--color-border); }
.application-updates__heading, .application-updates__actions { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.application-updates__heading { justify-content: space-between; }
.application-updates h2 { margin: 0; font-size: 18px; }
.application-updates small { font-weight: 400; font-size: 13px; color: #647a73; margin-left: 10px; }
.application-updates p { margin: 8px 0; color: #647a73; font-size: 14px; overflow-wrap: anywhere; }
.application-updates__preferences { display: grid; grid-template-columns: repeat(2, minmax(0, 280px)); gap: 0 32px; margin-top: 20px; border-top: 1px solid var(--color-border); }
.update-preference { display: flex; gap: 20px; align-items: center; justify-content: space-between; padding: 18px 0 0; font-size: 14px; cursor: pointer; }
.update-switch { position: relative; appearance: none; width: 40px; height: 24px; flex: 0 0 40px; margin: 0; padding: 0; border: 0; border-radius: 24px; background: #dce6e2; box-shadow: inset 0 0 0 1px #bfcdc7; cursor: pointer; transition: background-color 120ms; }
.update-switch::after { content: ""; position: absolute; left: 3px; top: 3px; width: 18px; height: 18px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px #16352b26; transition: transform 120ms; }
.update-switch:checked { background: var(--color-primary, #15958c); box-shadow: none; }
.update-switch:checked::after { transform: translateX(16px); }
.update-switch:focus-visible { outline: 2px solid #15958c; outline-offset: 3px; }
.update-switch:disabled { opacity: .55; cursor: wait; }
@media (max-width: 760px) { .application-updates__preferences { grid-template-columns: minmax(0, 1fr); } }
@media (prefers-reduced-motion: reduce) { .update-switch, .update-switch::after { transition: none; } }
.application-updates progress { width: 100%; height: 6px; accent-color: #14958e; margin-top: 16px; }
.application-updates .application-updates__message--error { color: #9b593b; }
</style>
