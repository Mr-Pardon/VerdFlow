<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from "vue";
import { ArrowUpRight, BookOpen, Check, Download, LoaderCircle, PackageOpen, RefreshCw, X } from "@lucide/vue";
import { useStarterResources } from "./useStarterResources";
import type { StarterImportItem } from "../../api/starterResources";

const emit = defineEmits<{ close: []; imported: [] }>();
const state = useStarterResources();
const dialog = ref<HTMLDialogElement>();
const labels: Record<StarterImportItem["status"], string> = {
  pending: "等待导入", downloading: "正在下载", importing: "正在导入", imported: "已导入", existing: "已存在", failed: "未完成", cancelled: "已取消",
};
const results = computed(() => new Map(state.operation.value?.items.map(item => [item.id, item]) ?? []));
const finished = computed(() => state.operation.value && !state.running.value);
const successful = computed(() => state.operation.value?.items.filter(item => ["imported", "existing"].includes(item.status)).length ?? 0);
const totalBytes = computed(() => state.manifest.value?.resources.reduce((sum, item) => sum + item.bytes, 0) ?? 0);
const size = (bytes: number) => bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
const close = () => { if (!state.running.value) emit("close"); };
watch(finished, value => { if (value) emit("imported"); });
onMounted(() => { dialog.value?.showModal(); void state.load(); });
onScopeDispose(() => dialog.value?.close());
</script>

<template>
  <dialog ref="dialog" class="starter-dialog" aria-labelledby="starter-title" @cancel.prevent="close">
    <header class="starter-dialog__header">
      <div><span class="guide-eyebrow">资源中心</span><h2 id="starter-title">准备推荐资源</h2></div>
      <button class="icon-button" title="关闭" aria-label="关闭" :disabled="state.running.value" @click="close"><X :size="18" /></button>
    </header>
    <div class="starter-dialog__body">
      <p class="starter-dialog__note">导入资产库与种植策略，开始配置你的第一份种植方案。</p>
      <p v-if="state.loading.value" class="starter-dialog__loading" role="status"><LoaderCircle class="spinning" :size="18" />正在读取推荐清单</p>
      <ul class="starter-resources">
        <li v-for="item in state.manifest.value?.resources" :key="item.id">
          <span class="starter-resource__icon"><PackageOpen v-if="item.kind === 'assets'" :size="22" /><BookOpen v-else :size="22" /></span>
          <div class="starter-resource__copy">
            <strong>{{ item.name }}</strong>
            <small>{{ item.kind === 'assets' ? '资产库 · VFA' : '种植策略 · VFS' }} · v{{ item.version }} · {{ size(item.bytes) }}</small>
            <progress v-if="results.get(item.id)?.status === 'downloading'" :value="results.get(item.id)?.downloaded_bytes" :max="item.bytes" :aria-label="`${item.name} 下载进度`" />
            <p v-if="results.get(item.id)?.message" class="guide-error">{{ results.get(item.id)?.message }}</p>
          </div>
          <span v-if="results.has(item.id)" class="starter-resource__state" :class="{ 'guide-error': results.get(item.id)?.status === 'failed' }">{{ labels[results.get(item.id)!.status] }}</span>
          <a :href="item.page_url" target="_blank" rel="noopener noreferrer" class="icon-button" :title="`查看 ${item.name} 资源说明`" :aria-label="`查看 ${item.name} 资源说明`"><ArrowUpRight :size="16" /></a>
        </li>
      </ul>
      <p v-if="state.manifest.value && !state.manifest.value.resources.length" class="starter-dialog__note">当前暂无推荐资源，可从资源中心选择下载。</p>
      <p class="starter-dialog__notice">策略导入后仍需确认信任。DTS 模型本体未包含在资产库中，配套 PAK 请从资产资源说明页获取。</p>
      <a class="text-button" href="https://mr-pardon.github.io/verdflow-resources/" target="_blank" rel="noopener noreferrer">浏览资源中心<ArrowUpRight :size="14" /></a>
      <p v-if="state.error.value" role="alert" class="guide-error">{{ state.error.value }}</p>
      <p v-if="finished" role="status" class="starter-dialog__result"><Check :size="17" />{{ successful }} / {{ state.operation.value?.items.length }} 项资源已就绪{{ successful < (state.operation.value?.items.length ?? 0) ? '，未完成的项目可重试' : '' }}</p>
    </div>
    <footer class="starter-dialog__footer">
      <span>{{ size(totalBytes) }}</span>
      <button v-if="state.error.value && state.running.value" class="secondary-button" @click="state.poll"><RefreshCw :size="16" />刷新状态</button>
      <button v-if="state.running.value" class="secondary-button" :disabled="state.submitting.value || state.operation.value?.status === 'cancelling'" @click="state.cancel"><X :size="16" />{{ state.operation.value?.status === 'cancelling' ? '正在取消' : '取消导入' }}</button>
      <template v-else>
        <button class="text-button" @click="close">{{ finished ? '返回引导' : '稍后再说' }}</button>
        <button v-if="!state.manifest.value" class="secondary-button" :disabled="state.loading.value" @click="state.load"><RefreshCw :size="16" />重新读取</button>
        <button v-else-if="!finished || successful < state.manifest.value.resources.length" class="primary-button" :disabled="!state.manifest.value.resources.length" @click="state.start"><Download :size="16" />{{ finished ? '重试导入' : '一键导入' }}</button>
      </template>
    </footer>
  </dialog>
</template>

<style scoped>
.starter-dialog { width: min(620px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 0; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-surface); color: var(--color-text); }
.starter-dialog[open] { display: flex; flex-direction: column; }
.starter-dialog::backdrop { background: #142e2a66; }
.starter-dialog__header, .starter-dialog__footer { display: flex; align-items: center; gap: 12px; padding: 20px 24px; flex-shrink: 0; }
.starter-dialog__header { justify-content: space-between; border-bottom: 1px solid var(--color-border); }
.starter-dialog h2 { margin: 5px 0 0; font-size: 22px; }
.starter-dialog__body { padding: 18px 24px; overflow-y: auto; min-height: 0; }
.starter-dialog p { font-size: 13px; line-height: 1.7; }
.starter-dialog__note, .starter-dialog__notice { color: var(--color-text-muted); margin: 0; }
.starter-dialog__notice { padding-top: 16px; }
.starter-resources { margin: 18px 0 0; padding: 0; list-style: none; }
.starter-resources li { display: flex; align-items: center; gap: 12px; padding: 17px 0; border-bottom: 1px solid var(--color-border); }
.starter-resource__icon { display: grid; place-items: center; background: var(--color-accent-soft); color: var(--color-accent); width: 42px; height: 42px; border-radius: 6px; flex-shrink: 0; }
.starter-resource__copy { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.starter-resource__copy strong { font-size: 14px; display: block; }
.starter-resource__copy small { display: block; color: var(--color-text-muted); font-size: 12px; margin-top: 6px; }
.starter-resource__copy progress { display: block; width: 100%; height: 5px; margin-top: 9px; accent-color: var(--color-accent); }
.starter-resource__state { flex-shrink: 0; font-size: 12px; }
.starter-dialog__footer { border-top: 1px solid var(--color-border); flex-wrap: wrap; justify-content: flex-end; }
.starter-dialog__footer > span { margin-right: auto; color: var(--color-text-muted); font-size: 12px; }
.starter-dialog__result, .starter-dialog__loading { display: flex; gap: 8px; align-items: center; color: var(--color-accent); }
@media (max-width: 480px) { .starter-dialog__body, .starter-dialog__header, .starter-dialog__footer { padding: 16px; } .starter-resource__icon { display: none; } .starter-resources li { gap: 8px; } }
</style>
