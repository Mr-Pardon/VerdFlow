<script setup lang="ts" generic="T extends BrowserAsset">
import { useAssetBrowser } from "./useAssetBrowser";
import { Check, ChevronDown, ChevronLeft, ChevronRight, Database, Search, RefreshCw, Trees } from "@lucide/vue";
import type { BrowserAsset, BrowserCatalog, AssetSearchPage, AssetSearchRequest } from "./types";

const props = withDefaults(defineProps<{
  catalogs: BrowserCatalog[];
  targetType?: string;
  providerId?: string;
  initialCatalogIds?: string[];
  selectedIds: string[];
  search: (request: AssetSearchRequest, signal: AbortSignal) => Promise<AssetSearchPage<T>>;
  mapAsset?: (asset: T) => T;
  assetLabel?: (asset: T) => string;
  disabled?: boolean;
}>(), { targetType: "dts", initialCatalogIds: () => [] });
const emit = defineEmits<{ toggle: [asset: T] }>();
const { root, grid, scopeOpen, chosenProvider, providers, compatible, scope, query, category,
  offset, pageSize, result, busy, error, selected, items, categoryKey, groups, scopeLabel, setScope, toggleScope, load } = useAssetBrowser(props);
const setRoot = (element: unknown) => { root.value = element as HTMLElement | null; };
const setGrid = (element: unknown) => { grid.value = element as HTMLElement | null; };
</script>

<template>
  <section :ref="setRoot" class="vf-asset-browser" aria-label="选择植被" @keydown.esc="scopeOpen = false">
    <div class="vf-browser-tools">
      <div class="vf-browser-scope">
        <button type="button" class="vf-scope-trigger" :aria-expanded="scopeOpen" aria-label="选择检索资产库" @click="scopeOpen = !scopeOpen">
          <Database :size="16" /><span>{{ scopeLabel }}</span><ChevronDown :size="14" />
        </button>
        <div v-if="scopeOpen" class="vf-scope-menu">
          <select v-if="!providerId && providers.length > 1" v-model="chosenProvider" aria-label="资产 Provider">
            <option v-for="id in providers" :key="id" :value="id">{{ id }}</option>
          </select>
          <div class="vf-scope-actions"><strong>检索范围</strong><button type="button" @click="setScope(compatible.map(c => c.catalog_id))">全选</button><button type="button" @click="setScope([])">清空</button></div>
          <div class="vf-scope-options">
            <label v-for="catalog in compatible" :key="catalog.catalog_id"><input type="checkbox" :checked="scope.includes(catalog.catalog_id)" @change="toggleScope(catalog.catalog_id)" /><span>{{ catalog.display_name }}</span></label>
          </div>
          <p v-if="!compatible.length">暂无可用资产库</p>
        </div>
      </div>
      <label class="vf-browser-search"><Search :size="16" /><input v-model="query" type="search" aria-label="搜索植被" placeholder="搜索名称、ID 或标签" /></label>
      <select v-model="category" class="vf-browser-category" aria-label="植被分类">
        <option value="">全部分类</option>
        <optgroup v-for="group in groups" :key="group.catalog_id" :label="group.display_name">
          <option v-for="item in group.collections" :key="item.collection_id" :value="categoryKey(group.catalog_id, item.collection_id)">{{ item.display_name }} ({{ item.asset_count }})</option>
        </optgroup>
      </select>
    </div>
    <p v-if="result?.unavailable_catalogs.length" class="vf-browser-warning" role="status">{{ result.unavailable_catalogs.map(item => `${catalogs.find(c => c.catalog_id === item.catalog_id)?.display_name ?? item.catalog_id}：${item.message}`).join('；') }}<button type="button" title="重新加载植被" @click="load()"><RefreshCw :size="14" /></button></p>
    <div class="vf-browser-body" :aria-busy="busy">
      <div :ref="setGrid" class="vf-browser-grid">
        <button v-for="asset in items" :key="asset.asset_id" type="button" class="vf-browser-asset" :class="{ selected: selected.has(asset.asset_id) }" :disabled="disabled || busy" :aria-pressed="selected.has(asset.asset_id)" :title="`${asset.display_name} · ${asset.source_catalog_name ?? ''} · ${assetLabel?.(asset) ?? ''}`" @click="emit('toggle', asset)">
          <span class="vf-browser-thumb"><slot name="thumbnail" :asset="asset"><Trees :size="28" /></slot></span>
          <strong>{{ asset.display_name }}</strong><small>{{ asset.source_catalog_name }}</small>
          <Check v-if="selected.has(asset.asset_id)" class="vf-browser-check" :size="16" />
        </button>
      </div>
      <div v-if="busy || error || !items.length" class="vf-browser-state" role="status">
        <template v-if="busy">正在检索植被</template>
        <template v-else-if="error">{{ error }}<button type="button" title="重试检索" @click="load()"><RefreshCw :size="18" /></button></template>
        <template v-else>{{ !scope.length ? '请选择要检索的资产库' : '没有匹配的植被' }}</template>
      </div>
    </div>
    <footer class="vf-browser-pagination">
      <span>{{ result?.total_count ? `${offset + 1}–${offset + items.length} / ${result.total_count}` : '0 种植被' }}</span>
      <div><button type="button" title="上一页植被" :disabled="busy || !offset" @click="load(Math.max(0, offset - pageSize))"><ChevronLeft :size="16" /></button>
        <span>{{ Math.floor(offset / pageSize) + 1 }} / {{ Math.max(1, Math.ceil((result?.total_count ?? 0) / pageSize)) }}</span>
        <button type="button" title="下一页植被" :disabled="busy || result?.next_offset == null" @click="load(result?.next_offset ?? 0)"><ChevronRight :size="16" /></button></div>
    </footer>
  </section>
</template>

<style scoped>
.vf-asset-browser { --ab-border: var(--color-border, #cddbd8); --ab-accent: var(--color-accent, #15958d); --ab-muted: var(--color-text-muted, #6d807b); --ab-bg: var(--color-surface, #fff); display:flex; flex-direction:column; min-height:0; min-width:0; height:100%; color:var(--color-text, #203330); font-size:12px; letter-spacing:0; }
.vf-asset-browser button,.vf-asset-browser input,.vf-asset-browser select { font:inherit; letter-spacing:0; }
.vf-browser-tools { display:flex; flex-wrap:wrap; align-items:center; gap:8px; padding:12px; border-bottom:1px solid var(--ab-border); }
.vf-browser-scope { position:relative; min-width:0; flex:1 1 180px; }
.vf-scope-trigger,.vf-browser-category,.vf-browser-search { display:flex; align-items:center; gap:8px; min-height:36px; border:1px solid var(--ab-border); border-radius:4px; background:var(--ab-bg); color:inherit; padding:0 10px; }
.vf-scope-trigger { width:100%; text-align:left; cursor:pointer; }
.vf-scope-trigger span { flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.vf-scope-trigger svg { flex-shrink:0; }
.vf-browser-search { flex:2 1 210px; min-width:0; }
.vf-browser-search input { width:100%; min-width:0; border:0; outline:0; background:transparent; color:inherit; padding:0; height:34px; }
.vf-browser-search:focus-within { border-color:var(--ab-accent); }
.vf-browser-category { flex:1 1 125px; min-width:0; max-width:100%; }
.vf-scope-menu { position:absolute; z-index:10; left:0; top:42px; width:320px; max-width:min(80vw, 380px); padding:10px; background:var(--ab-bg); border:1px solid var(--ab-border); border-radius:6px; box-shadow:0 8px 24px #173a3320; }
.vf-scope-actions { display:flex; align-items:center; gap:10px; margin-bottom:8px; }
.vf-scope-actions strong { flex:1; }
.vf-scope-actions button { border:0; background:transparent; color:var(--ab-accent); cursor:pointer; padding:4px; }
.vf-scope-options { max-height:240px; overflow:auto; }
.vf-scope-options label { display:flex; align-items:center; gap:9px; padding:9px 4px; cursor:pointer; }
.vf-scope-options span { overflow-wrap:anywhere; min-width:0; }
.vf-scope-options input { width:15px; height:15px; accent-color:var(--ab-accent); flex-shrink:0; }
.vf-browser-body { position:relative; flex:1; min-height:120px; overflow:hidden; }
.vf-browser-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,132px),1fr)); gap:10px; align-content:start; overflow-y:auto; height:100%; min-height:0; padding:12px; box-sizing:border-box; }
.vf-browser-asset { position:relative; display:flex; flex-direction:column; gap:5px; min-width:0; padding:7px; border:1px solid var(--ab-border); background:var(--ab-bg); border-radius:5px; text-align:left; color:inherit; cursor:pointer; }
.vf-browser-asset:hover,.vf-browser-asset:focus-visible { border-color:var(--ab-accent); }
.vf-browser-asset.selected { border-color:var(--ab-accent); background:var(--color-accent-soft,#e4f3ef); }
.vf-browser-asset strong,.vf-browser-asset small { display:block; width:100%; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.vf-browser-asset small { color:var(--ab-muted); font-size:11px; }
.vf-browser-thumb { display:flex; align-items:center; justify-content:center; width:100%; aspect-ratio:1; border-radius:3px; overflow:hidden; background:#eef3f1; color:var(--ab-muted); }
.vf-browser-thumb :deep(img),.vf-browser-thumb :deep(> span) { width:100%; height:100%; object-fit:contain; }
.vf-browser-check { position:absolute; right:10px; top:10px; color:#fff; background:var(--ab-accent); border-radius:3px; padding:2px; box-sizing:content-box; }
.vf-browser-state { position:absolute; inset:0; display:flex; gap:12px; align-items:center; justify-content:center; padding:20px; background:var(--ab-bg); color:var(--ab-muted); text-align:center; overflow-wrap:anywhere; }
.vf-browser-pagination { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; padding:9px 12px; border-top:1px solid var(--ab-border); color:var(--ab-muted); }
.vf-browser-pagination div { display:flex; align-items:center; gap:9px; }
.vf-browser-pagination button,.vf-browser-state button,.vf-browser-warning button { display:inline-flex; align-items:center; justify-content:center; width:30px; height:30px; padding:0; background:var(--ab-bg); color:var(--ab-accent); border:1px solid var(--ab-border); border-radius:4px; cursor:pointer; }
.vf-asset-browser button:disabled { opacity:.45; cursor:default; }
.vf-browser-warning { margin:0; padding:8px 12px; background:#fff6e7; color:#846322; overflow-wrap:anywhere; }
.vf-browser-warning button { margin-left:8px; vertical-align:middle; }
</style>
