<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ArrowLeft, ArrowRight, BookOpen, Boxes, Check, ChevronDown, Compass, Download, LibraryBig, LoaderCircle, PackageOpen, Sprout, X } from "@lucide/vue";
import { activeConfirmation } from "../../components/confirmDialog";
import { guideSteps, type GuideWorkflow } from "./steps";
import type { FirstUseGuide } from "./useFirstUseGuide";
import StarterResourcesDialog from "./StarterResourcesDialog.vue";

const { guide } = defineProps<{ guide: FirstUseGuide }>();
const router = useRouter();
const dialog = ref<HTMLDialogElement>();
const starterOpen = ref(false);
const logo = `${import.meta.env.BASE_URL}brand/logo-light.png`;
const author = computed(() => "author" in guide.step.value ? guide.step.value.author : null);
const canResume = computed(() => guide.progress.value.status === "paused" && (guide.index.value > 0 || guide.progress.value.visited.length > 0));
const deferWelcome = () => canResume.value ? guide.pause() : guide.dismiss();
const needsSetup = computed(() => guide.page.value?.ready === false);
const isLastStep = computed(() => guide.index.value === guideSteps.length - 1);
const skipCurrent = computed(() => needsSetup.value || Boolean(guide.page.value?.error || guide.page.value?.loading));
const nextLabel = computed(() => guide.step.value.id === "runtimes"
  ? skipCurrent.value ? "稍后配置" : "准备资源"
  : skipCurrent.value ? "暂时跳过" : isLastStep.value ? "完成引导" : "下一步");
const skipped = computed(() => guideSteps.filter(step => guide.progress.value.skipped.includes(step.id)));
const workflows: { value: GuideWorkflow; label: string }[] = [
  { value: "live", label: "DTS 实时种植" }, { value: "fbx", label: "FBX 工作台" }, { value: "both", label: "两种都用" },
];
watch([dialog, guide.surface], ([element, surface]) => {
  if (!element) return;
  const modal = surface === "welcome" || surface === "finish";
  if (modal && !element.open) element.showModal();
  if (!modal && element.open) element.close();
}, { flush: "post", immediate: true });
onScopeDispose(() => dialog.value?.close());
watch(guide.activeStep, async (id, _, onCleanup) => {
  let cancelled = false;
  onCleanup(() => { cancelled = true; });
  await nextTick();
  if (id && !cancelled) document.querySelector(`[data-guide-anchor="${id}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
}, { flush: "post" });
const finish = async () => { guide.pause(); await router.push("/dts"); };
</script>

<template>
  <StarterResourcesDialog v-if="starterOpen" @close="starterOpen = false" @imported="guide.page.value?.refresh?.()" />
  <dialog ref="dialog" class="guide-welcome" :aria-labelledby="guide.surface.value === 'finish' ? 'guide-finish-title' : 'guide-welcome-title'" @cancel.prevent="guide.surface.value === 'finish' ? guide.pause() : deferWelcome()">
    <header class="guide-welcome__brand">
      <img :src="logo" alt="VerdFlow" width="141" height="28" />
      <button class="icon-button" type="button" title="稍后再看" aria-label="稍后再看" @click="guide.surface.value === 'finish' ? guide.pause() : deferWelcome()"><X :size="18" /></button>
    </header>
    <template v-if="guide.surface.value !== 'finish'">
      <div class="guide-welcome__body">
        <span class="guide-eyebrow">第一次使用</span>
        <h2 id="guide-welcome-title">准备第一次种植</h2>
        <p>VerdFlow 用资产和策略完成种植。所需资源可从内置资源中心获取。</p>
        <ol class="guide-overview">
          <li><Boxes :size="21" /><div><strong>资产库</strong><span>决定能种什么，记录可用的植被模型。</span></div></li>
          <li><BookOpen :size="21" /><div><strong>种植策略</strong><span>决定怎么种，例如成片分布或沿线排列。</span></div></li>
          <li><Sprout :size="21" /><div><strong>种植方案 · 可选</strong><span>复用搭配与参数，不必每次从头配置。</span></div></li>
        </ol>
        <p class="guide-note">先选工作方式，再按需准备环境和资源。可以随时暂停，从设置继续。</p>
      </div>
      <footer class="guide-welcome__footer">
        <button class="text-button" type="button" @click="deferWelcome">{{ canResume ? '稍后继续' : '暂时跳过' }}</button>
        <button v-if="canResume" class="text-button" type="button" :disabled="guide.busy.value" @click="guide.start(true)">从头了解</button>
        <button class="primary-button" type="button" autofocus :disabled="guide.busy.value" @click="guide.start()">{{ canResume ? '继续引导' : '选择工作方式' }}<ArrowRight :size="17" /></button>
      </footer>
    </template>
    <template v-else>
      <div class="guide-welcome__body">
        <span class="guide-eyebrow">引导结束</span>
        <h2 id="guide-finish-title">开始你的第一片绿地</h2>
        <p>接下来可以从 DTS 工作台进入种植。需要新方案或策略新版时，再回资源中心。</p>
        <RouterLink class="text-button guide-finish-resource" to="/resources" @click="guide.pause"><LibraryBig :size="16" />资源中心<ArrowRight :size="14" /></RouterLink>
        <div v-if="skipped.length" class="guide-deferred">
          <strong>这些步骤留待之后</strong>
          <button v-for="step in skipped" :key="step.id" type="button" @click="guide.go(step.id)">{{ step.title }}<ArrowRight :size="15" /></button>
        </div>
        <p class="guide-note">完成引导不代表环境已经全部就绪。进入工作路径后，仍会按实际配置检查。</p>
      </div>
      <footer class="guide-welcome__footer">
        <button class="text-button" type="button" @click="guide.pause">留在此页</button>
        <button class="primary-button" type="button" @click="finish">前往 DTS 工作台<ArrowRight :size="17" /></button>
      </footer>
    </template>
  </dialog>

  <aside v-if="guide.surface.value === 'step' && guide.onStepPage.value && !activeConfirmation" class="guide-coach" aria-label="新手引导">
    <header class="guide-coach__header">
      <span><Compass :size="16" />新手引导 <small>{{ guide.index.value + 1 }} / {{ guideSteps.length }}</small></span>
      <div>
        <button class="icon-button" type="button" title="收起引导" aria-label="收起引导" @click="guide.surface.value = 'collapsed'"><ChevronDown :size="17" /></button>
        <button class="icon-button" type="button" title="暂停引导，可从设置继续" aria-label="暂停引导" @click="guide.pause"><X :size="17" /></button>
      </div>
    </header>
    <div class="guide-coach__body">
      <span class="guide-eyebrow">{{ guide.step.value.section }}</span>
      <h2 aria-live="polite">{{ guide.step.value.title }}</h2>
      <p>{{ guide.step.value.description }}</p>
      <fieldset v-if="guide.step.value.id === 'runtimes'" class="guide-workflows">
        <legend>主要使用哪条路径？</legend>
        <label v-for="workflow in workflows" :key="workflow.value" :class="{ selected: guide.progress.value.workflow === workflow.value }">
          <input type="radio" name="guide-workflow" :value="workflow.value" :checked="guide.progress.value.workflow === workflow.value" @change="guide.setWorkflow(workflow.value)" />{{ workflow.label }}
        </label>
      </fieldset>
      <p class="guide-note">{{ guide.step.value.hint }}</p>
      <button v-if="guide.step.value.id === 'resources'" class="primary-button" type="button" :disabled="guide.busy.value" @click="starterOpen = true"><Download :size="16" />一键导入推荐资源</button>
      <div v-if="author" class="guide-author">
        <strong><PackageOpen :size="16" />{{ author.name }} <small>进阶创作 · 可选</small></strong>
        <p>{{ author.description }}</p>
        <button v-if="guide.page.value?.authorAction" class="text-button" type="button" :disabled="guide.busy.value || guide.page.value.authorAction.disabled" @click="guide.run(guide.page.value.authorAction)"><Download :size="14" />导出 {{ author.name }}</button>
      </div>
      <div v-if="guide.page.value" class="guide-readiness" :class="{ 'guide-readiness--ready': guide.page.value.ready && !guide.page.value.error }" role="status">
        <LoaderCircle v-if="guide.page.value.loading" :size="15" class="spinning" />
        <Check v-else-if="guide.page.value.ready && !guide.page.value.error" :size="15" />
        <span>{{ guide.page.value.loading ? '正在读取当前状态' : guide.page.value.error ? '状态未完整读取，请查看页面提示后重试。' : guide.page.value.status }}</span>
      </div>
      <div v-if="guide.page.value?.actions?.length || guide.step.value.id === 'plans'" class="guide-actions">
        <button v-for="action in guide.page.value?.actions" :key="action.label" :class="action.primary ? 'primary-button' : 'secondary-button'" type="button" :disabled="guide.busy.value || action.disabled" @click="guide.run(action)">{{ action.label }}<ArrowRight :size="14" /></button>
        <RouterLink v-if="guide.step.value.id === 'plans'" class="primary-button" to="/resources"><LibraryBig :size="14" />前往资源中心<ArrowRight :size="14" /></RouterLink>
      </div>
      <p v-if="guide.error.value" role="alert" class="guide-error">{{ guide.error.value }}</p>
    </div>
    <footer class="guide-coach__footer">
      <button class="icon-button" type="button" aria-label="上一步" title="上一步" :disabled="guide.index.value === 0 || guide.busy.value" @click="guide.previous"><ArrowLeft :size="17" /></button>
      <span>{{ needsSetup ? '也可以稍后配置' : '随时可暂停' }}</span>
      <button class="primary-button" type="button" :disabled="guide.busy.value" @click="guide.next(skipCurrent)">{{ nextLabel }}<ArrowRight :size="16" /></button>
    </footer>
  </aside>
  <button v-else-if="guide.surface.value === 'collapsed' && !activeConfirmation" class="guide-resume" type="button" :disabled="guide.busy.value" :title="guide.error.value || '回到当前引导步骤'" @click="guide.go(guide.step.value.id)"><Compass :size="18" />继续引导 <small>{{ guide.index.value + 1 }} / {{ guideSteps.length }}</small><ArrowRight :size="15" /></button>
</template>
