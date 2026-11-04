<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowLeft, ArrowUpRight, BookOpen, Boxes, Check, ChevronRight, Download, ExternalLink, ImageOff, LibraryBig, LoaderCircle, RefreshCw, Search, Sprout, X } from "@lucide/vue";
import type { ResourceKind, ResourceSelection } from "../../api/resourceCenter";
import StarterResourcesDialog from "../onboarding/StarterResourcesDialog.vue";
import { useResourceCenter } from "./useResourceCenter";
import { centerUrl, formatBytes, kindLabels, renderResourceMarkdown, safeContentImage, stateLabels } from "./resourcePresentation";

const route = useRoute();
const router = useRouter();
const state = useResourceCenter();
const query = ref("");
const tab = ref<"all" | ResourceKind>("all");
const detailTab = ref("overview");
const brokenImages = ref(new Set<string>());
const selection = ref<ResourceSelection>();
const resourceId = computed(() => String(route.params.resourceId ?? ""));
const requestedVersion = computed(() => typeof route.query.version === "string" ? route.query.version : "");
const resources = computed(() => state.catalog.value?.resources ?? []);
const tabs = computed(() => [
  { id: "all", label: "全部资源", icon: LibraryBig, count: resources.value.length },
  ...(["assets", "strategies", "plans"] as const).map(kind => ({ id: kind, label: kindLabels[kind], icon: icons[kind], count: resources.value.filter(r => r.kind === kind).length })),
]);
const icons = { assets: Boxes, strategies: BookOpen, plans: Sprout };
const filtered = computed(() => resources.value.filter(r => (tab.value === "all" || r.kind === tab.value)
  && [r.name, r.summary, r.author, ...r.tags].join(" ").toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())));
const detail = computed(() => state.detail.value);
const version = computed(() => detail.value?.versions.find(v => v.version === detail.value?.selected_version));
const markdown = computed(() => detail.value ? renderResourceMarkdown(detail.value.readme, detail.value.content_base) : "");
const localTarget = computed(() => detail.value?.resource.kind === "assets" ? "/assets" : detail.value?.resource.kind === "plans" ? "/plans" : "/strategies");
const cover = (url: string) => !brokenImages.value.has(url) ? safeContentImage(url) : "";
const failImage = (url: string) => { brokenImages.value = new Set([...brokenImages.value, url]); };
const openResource = (id: string, v = "") => router.push({ path: `/resources/${encodeURIComponent(id)}`, query: v ? { version: v } : {} });
const selectVersion = (event: Event) => openResource(resourceId.value, (event.target as HTMLSelectElement).value);
const refresh = async () => { await state.load(true); if (resourceId.value) await state.open(resourceId.value, requestedVersion.value); };
const imported = async () => { await state.load(); if (resourceId.value) await state.open(resourceId.value, requestedVersion.value); };
const importResource = () => {
  if (detail.value && version.value?.installable) selection.value = { resource_id: resourceId.value, version: version.value.version, revision: detail.value.revision };
};
watch([resourceId, requestedVersion], ([id, v]) => { detailTab.value = "overview"; void state.open(id, v); }, { immediate: true });
onMounted(() => void state.load());
</script>

<template>
  <section class="resource-center">
    <header class="page-header resource-header">
      <div class="resource-heading">
        <button v-if="resourceId" class="icon-button" title="返回资源中心" aria-label="返回资源中心" @click="router.push('/resources')"><ArrowLeft :size="19" /></button>
        <div><span class="page-header__eyebrow">{{ resourceId ? '资源中心' : '发现与创作' }}</span><h1>{{ resourceId ? detail?.resource.name || '资源详情' : '资源中心' }}</h1></div>
      </div>
      <div class="page-actions">
        <button class="icon-button" title="刷新资源中心" aria-label="刷新资源中心" :disabled="state.loading.value || state.loadingDetail.value" @click="refresh"><RefreshCw :size="17" :class="{ spinning: state.loading.value || state.loadingDetail.value }" /></button>
        <a class="secondary-button" :href="resourceId ? `${centerUrl}?id=${encodeURIComponent(resourceId)}` : centerUrl" target="_blank" rel="noopener noreferrer"><ExternalLink :size="15" />网页版</a>
      </div>
    </header>
    <div v-if="state.error.value || state.warning.value" class="resource-message" :class="{ 'resource-message--error': state.error.value }" role="alert">
      <span>{{ state.error.value || state.warning.value }}</span>
      <button class="text-button" @click="refresh">重试</button>
      <button class="icon-button" title="关闭提示" aria-label="关闭提示" @click="state.error.value = ''; state.warning.value = ''"><X :size="16" /></button>
    </div>

    <template v-if="!resourceId">
      <div class="resource-toolbar">
        <div class="resource-tabs" role="tablist" aria-label="资源类型">
          <button v-for="item in tabs" :key="item.id" role="tab" :aria-selected="tab === item.id" :class="{ active: tab === item.id }" @click="tab = item.id as typeof tab">
            <component :is="item.icon" :size="17" /><span>{{ item.label }}</span><small>{{ item.count }}</small>
          </button>
        </div>
        <label class="search-control"><Search :size="17" /><span class="sr-only">搜索资源</span><input v-model="query" type="search" placeholder="搜索名称、用途或作者" /></label>
      </div>
      <div class="resource-scroll">
        <div v-if="state.loading.value && !resources.length" class="resource-empty" role="status"><LoaderCircle :size="26" class="spinning" /><strong>正在读取资源中心</strong></div>
        <div v-else-if="!filtered.length" class="resource-empty"><Search :size="28" /><strong>{{ query ? '没有找到相关资源' : '暂时没有可展示的资源' }}</strong><button class="text-button" @click="query = ''; tab = 'all'; state.load(true)">重新查看</button></div>
        <div v-else class="resource-grid">
          <button v-for="item in filtered" :key="item.id" class="resource-card" @click="openResource(item.id)">
            <div class="resource-card__media">
              <img v-if="cover(item.cover)" :src="cover(item.cover)" :alt="item.name" loading="lazy" referrerpolicy="no-referrer" @error="failImage(item.cover)" />
              <component v-else :is="icons[item.kind]" class="resource-card__fallback" :size="40" :stroke-width="1.2" />
            </div>
            <div class="resource-card__body">
              <div class="resource-card__meta"><span><component :is="icons[item.kind]" :size="14" />{{ kindLabels[item.kind] }}</span><small>v{{ item.version }}</small></div>
              <h2>{{ item.name }}<ChevronRight :size="17" /></h2>
              <p>{{ item.summary }}</p>
              <div class="resource-card__tags"><span v-for="tag in item.tags.slice(0, 3)" :key="tag">{{ tag }}</span></div>
              <footer><span>{{ item.author }}</span><span v-if="item.local_state !== 'available'" class="resource-local" :data-state="item.local_state"><Check v-if="item.local_state === 'installed'" :size="13" />{{ stateLabels[item.local_state] }}</span><span v-else>{{ item.platforms.join(' · ') }}</span></footer>
            </div>
          </button>
        </div>
      </div>
      <footer class="resource-count"><span>{{ filtered.length }} 项资源</span><span>来自 VerdFlow 资源中心</span></footer>
    </template>

    <div v-else-if="state.loadingDetail.value" class="resource-empty" role="status"><LoaderCircle :size="26" class="spinning" /><strong>正在读取资源详情</strong></div>
    <div v-else-if="detail && version" class="resource-scroll resource-detail-scroll">
      <div class="resource-detail-layout">
        <main class="resource-article">
          <div class="resource-detail-cover"><img v-if="cover(detail.resource.cover)" :src="cover(detail.resource.cover)" :alt="detail.resource.name" referrerpolicy="no-referrer" @error="failImage(detail.resource.cover)" /><ImageOff v-else :size="40" /></div>
          <div class="resource-tabs resource-detail-tabs" role="tablist" aria-label="资源详情">
            <button role="tab" :aria-selected="detailTab === 'overview'" :class="{ active: detailTab === 'overview' }" @click="detailTab = 'overview'">资源说明</button>
            <button role="tab" :aria-selected="detailTab === 'changes'" :class="{ active: detailTab === 'changes' }" @click="detailTab = 'changes'">版本记录<span>{{ detail.versions.length }}</span></button>
          </div>
          <article v-if="detailTab === 'overview'" class="resource-markdown" v-html="markdown" />
          <div v-else class="resource-history">
            <section v-for="release in detail.versions" :key="release.version"><header><strong>v{{ release.version }}</strong><small>{{ release.released_at }}</small><span v-if="release.version === detail.resource.version">推荐版本</span></header><p>{{ release.notes || '暂无补充说明。' }}</p><button v-if="release.version !== version.version" class="text-button" @click="openResource(resourceId, release.version)">查看此版本<ChevronRight :size="15" /></button></section>
          </div>
        </main>
        <aside class="resource-facts">
          <div class="resource-facts__kind"><component :is="icons[detail.resource.kind]" :size="18" />{{ kindLabels[detail.resource.kind] }}<span>{{ detail.resource.platforms.join(' · ') }}</span></div>
          <h2>{{ detail.resource.name }}</h2><p class="resource-summary">{{ detail.resource.summary }}</p>
          <label class="resource-version-label">版本<select :value="version.version" aria-label="资源版本" @change="selectVersion"><option v-for="release in detail.versions" :key="release.version" :value="release.version">v{{ release.version }}{{ release.version === detail.resource.version ? ' · 推荐' : '' }}</option></select></label>
          <div class="resource-download-meta"><span>{{ version.released_at }}</span><span v-if="version.bytes">{{ formatBytes(version.bytes) }}</span></div>
          <button v-if="version.local_state !== 'installed'" class="primary-button resource-import" :disabled="!version.installable || (detail.resource.kind === 'strategies' && version.local_state === 'conflict')" @click="importResource"><Download :size="17" />{{ !version.installable ? '暂不支持应用内导入' : version.local_state === 'conflict' && detail.resource.kind === 'strategies' ? '同版本内容冲突' : '导入 VerdFlow' }}</button>
          <RouterLink v-else class="secondary-button resource-import" :to="localTarget"><Check :size="17" />已导入 · 查看本地</RouterLink>
          <p v-if="version.local_state === 'conflict'" class="resource-caution">本地存在不同内容，导入前请核对，现有资源不会被静默覆盖。</p>
          <p v-else-if="version.local_state === 'local'" class="resource-caution">本地已有此资产库。导入时将核对内容，不覆盖本地修改。</p>
          <p v-else-if="detail.resource.kind === 'strategies'" class="resource-subtle">新旧版本并存。导入后需在策略库确认信任。</p>
          <section class="resource-release"><h3>本版更新 <span>v{{ version.version }}</span></h3><p>{{ version.notes || '暂无补充说明。' }}</p></section>
          <section v-if="version.relations.length" class="resource-related"><h3>关联资源</h3>
            <div v-for="relation in version.relations" :key="relation.kind + relation.name">
              <button v-if="relation.resource_id" class="resource-relation-link" @click="openResource(relation.resource_id, relation.version)"><span><small>{{ relation.kind === 'strategy' ? '种植策略' : '来源资产库 · 可选' }}</small><strong>{{ relation.name }}</strong></span><ChevronRight :size="16" /></button>
              <a v-else-if="relation.url" class="resource-relation-link" :href="relation.url" target="_blank" rel="noopener noreferrer"><span><small>配套 PAK · 外部下载</small><strong>{{ relation.name }}</strong></span><ArrowUpRight :size="16" /></a>
              <p v-if="relation.note">{{ relation.note }}</p>
            </div>
          </section>
          <dl class="resource-credits"><dt>作者</dt><dd>{{ detail.resource.author }}</dd><dt>许可</dt><dd>{{ detail.license }}</dd></dl>
        </aside>
      </div>
    </div>
    <StarterResourcesDialog v-if="selection" :selection="selection" @close="selection = undefined" @imported="imported" />
  </section>
</template>

<style scoped>
.resource-center { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 20px; padding: 30px 32px 20px; }
.resource-header { flex-shrink: 0; margin: 0; gap: 16px; }
.resource-heading { display: flex; align-items: center; gap: 14px; min-width: 0; }
.resource-heading h1 { overflow-wrap: anywhere; }
.resource-header .page-actions { display: flex; flex-shrink: 0; width: auto; flex-wrap: nowrap; }
.resource-header .secondary-button { white-space: nowrap; text-decoration: none; }
.resource-message { display: flex; align-items: center; gap: 12px; background: #fff9ec; border: 1px solid #e8d4ab; padding: 10px 14px; font-size: 13px; border-radius: 6px; }
.resource-message span { flex: 1; overflow-wrap: anywhere; }
.resource-message--error { color: #995138; }
.resource-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px 20px; flex-shrink: 0; border-bottom: 1px solid var(--color-border); }
.resource-tabs { display: flex; gap: 22px; align-items: stretch; }
.resource-tabs button { display: inline-flex; align-items: center; gap: 7px; padding: 13px 0; border: 0; border-bottom: 2px solid transparent; background: none; color: var(--color-text-muted); font-size: 14px; cursor: pointer; white-space: nowrap; }
.resource-tabs button.active { border-bottom-color: var(--color-accent); color: var(--color-accent); font-weight: 650; }
.resource-tabs small, .resource-tabs button > span:last-child:not(:nth-child(2)) { font-size: 11px; color: var(--color-text-faint); }
.resource-toolbar .search-control { margin-bottom: 10px; width: min(290px, 100%); }
.resource-scroll { flex: 1; min-height: 0; overflow: auto; padding: 0 6px 16px 0; scrollbar-width: thin; }
.resource-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(270px, 100%), 1fr)); gap: 22px; align-content: start; }
.resource-card { display: flex; flex-direction: column; min-width: 0; padding: 0; text-align: left; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px; overflow: hidden; cursor: pointer; color: var(--color-text); transition: border-color .15s, box-shadow .15s; }
.resource-card:hover, .resource-card:focus-visible { border-color: var(--color-accent); box-shadow: 0 3px 12px #183b2c0c; }
.resource-card__media { position: relative; aspect-ratio: 16/9; width: 100%; display: grid; place-items: center; background: #edf2f0; border-bottom: 1px solid var(--color-border); overflow: hidden; }
.resource-card__media img { position: absolute; inset: 0; display: block; width: 100%; height: 100%; object-fit: contain; }
.resource-card__fallback { color: #6c8b80; }
.resource-card__body { display: flex; flex-direction: column; flex: 1; padding: 18px 18px 0; min-width: 0; }
.resource-card__meta, .resource-card footer { display: flex; justify-content: space-between; gap: 10px; font-size: 12px; color: var(--color-text-muted); }
.resource-card__meta > span, .resource-local { display: inline-flex; align-items: center; gap: 5px; }
.resource-card h2 { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin: 12px 0 8px; font-size: 17px; line-height: 1.5; overflow-wrap: anywhere; }
.resource-card h2 svg { flex-shrink: 0; color: var(--color-text-faint); }
.resource-card p { font-size: 13px; line-height: 1.75; color: var(--color-text-muted); margin: 0; min-height: 68px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.resource-card__tags { display: flex; flex-wrap: wrap; gap: 7px; margin: 14px 0 18px; color: #567067; font-size: 11px; }
.resource-card__tags span { background: #f1f5f3; padding: 3px 6px; border-radius: 3px; }
.resource-card footer { border-top: 1px solid var(--color-border); padding: 13px 0; margin-top: auto; }
.resource-local[data-state="installed"], .resource-local[data-state="local"] { color: var(--color-accent); }
.resource-local[data-state="conflict"] { color: #ac7031; }
.resource-count { display: flex; justify-content: space-between; gap: 12px; flex-shrink: 0; font-size: 12px; color: var(--color-text-faint); }
.resource-empty { flex: 1; min-height: 220px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px; color: var(--color-text-muted); }
.resource-empty strong { font-size: 15px; font-weight: 500; }
.resource-detail-layout { display: grid; grid-template-columns: minmax(0, 1fr) 310px; gap: 32px; max-width: 1400px; margin: 0 auto; }
.resource-article { min-width: 0; }
.resource-detail-cover { position: relative; aspect-ratio: 16/9; max-height: 460px; display: grid; place-items: center; background: #edf2f0; border: 1px solid var(--color-border); border-radius: 6px; overflow: hidden; }
.resource-detail-cover img { position: absolute; inset: 0; display: block; width: 100%; height: 100%; object-fit: contain; }
.resource-detail-tabs { border-bottom: 1px solid var(--color-border); margin-top: 18px; }
.resource-facts { min-width: 0; padding: 4px 0 24px; }
.resource-facts__kind { display: flex; align-items: center; gap: 7px; color: var(--color-accent); font-size: 12px; }
.resource-facts__kind > span { margin-left: auto; color: var(--color-text-muted); }
.resource-facts h2 { font-size: 22px; line-height: 1.5; margin: 14px 0 10px; overflow-wrap: anywhere; }
.resource-summary { font-size: 13px; line-height: 1.9; color: var(--color-text-muted); margin: 0 0 24px; }
.resource-version-label { display: flex; gap: 15px; align-items: center; font-size: 13px; }
.resource-version-label select { flex: 1; min-width: 0; padding: 9px 12px; background: var(--color-surface); border: 1px solid var(--color-border-strong); border-radius: 5px; color: var(--color-text); font: inherit; }
.resource-download-meta { display: flex; justify-content: space-between; font-size: 11px; color: var(--color-text-muted); margin: 12px 0 17px; }
.resource-import { width: 100%; justify-content: center; box-sizing: border-box; }
.resource-subtle, .resource-caution { font-size: 12px; line-height: 1.7; color: var(--color-text-muted); margin: 10px 0 0; }
.resource-caution { color: #a4712f; }
.resource-release, .resource-related { border-top: 1px solid var(--color-border); padding-top: 20px; margin-top: 24px; }
.resource-facts h3 { display: flex; justify-content: space-between; font-size: 13px; margin: 0 0 12px; }
.resource-facts h3 span { font-size: 11px; font-weight: 400; color: var(--color-text-muted); }
.resource-release p, .resource-related p { font-size: 12px; line-height: 1.85; color: var(--color-text-muted); white-space: pre-line; margin: 7px 0 0; }
.resource-related > div + div { margin-top: 18px; }
.resource-relation-link { display: flex; align-items: center; justify-content: space-between; gap: 12px; background: none; padding: 0; border: 0; width: 100%; text-align: left; text-decoration: none; color: var(--color-text); cursor: pointer; }
.resource-relation-link small { display: block; font-size: 11px; color: var(--color-text-muted); margin-bottom: 5px; }
.resource-relation-link strong { font-size: 13px; font-weight: 550; overflow-wrap: anywhere; }
.resource-relation-link svg { flex-shrink: 0; color: var(--color-accent); }
.resource-credits { display: grid; grid-template-columns: 30px 1fr; gap: 12px 14px; font-size: 12px; line-height: 1.7; margin: 24px 0 0; padding-top: 20px; border-top: 1px solid var(--color-border); }
.resource-credits dt { color: var(--color-text-faint); }
.resource-credits dd { margin: 0; overflow-wrap: anywhere; color: var(--color-text-muted); }
.resource-history section { padding: 24px 0; border-bottom: 1px solid var(--color-border); }
.resource-history header { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.resource-history small { color: var(--color-text-muted); }
.resource-history header > span { font-size: 11px; color: var(--color-accent); }
.resource-history p { font-size: 13px; line-height: 1.9; white-space: pre-line; }
.resource-markdown { padding: 24px 0; font-size: 14px; line-height: 1.9; overflow-wrap: anywhere; }
.resource-markdown :deep(h1) { font-size: 22px; margin: 0 0 16px; }
.resource-markdown :deep(h2) { font-size: 18px; margin: 26px 0 12px; }
.resource-markdown :deep(h3) { font-size: 15px; margin: 20px 0 10px; }
.resource-markdown :deep(img) { display: block; max-width: 100%; max-height: 520px; object-fit: contain; margin: 20px auto; }
.resource-markdown :deep(table) { display: block; width: 100%; overflow: auto; border-collapse: collapse; font-size: 13px; }
.resource-markdown :deep(th), .resource-markdown :deep(td) { text-align: left; padding: 10px 14px; border: 1px solid var(--color-border); }
.resource-markdown :deep(a) { color: var(--color-accent); }
.resource-markdown :deep(pre) { overflow: auto; padding: 14px; background: #edf2f0; }
.resource-markdown :deep(blockquote) { border-left: 3px solid var(--color-border-strong); margin: 16px 0; padding-left: 16px; color: var(--color-text-muted); }
@media (max-width: 1100px) { .resource-detail-layout { grid-template-columns: minmax(0, 1fr) 270px; gap: 22px; } .resource-toolbar .search-control { width: 100%; } }
@media (max-width: 780px) { .resource-detail-layout { display: flex; flex-direction: column; } .resource-facts { order: -1; } .resource-tabs { gap: 15px; overflow-x: auto; max-width: 100%; } .resource-tabs button { font-size: 12px; } .resource-header { flex-wrap: wrap; } .resource-heading h1 { font-size: 24px; } .resource-center { gap: 16px; padding: 20px 18px; } }
</style>
