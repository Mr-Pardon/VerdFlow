<script setup lang="ts">
import {
  ArrowLeft,
  Box,
  Boxes,
  ChevronRight,
  Layers3,
  LoaderCircle,
  PackageOpen,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Trees,
  Upload,
  Download,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";

import type { AssetCatalogAssetInfo } from "../../api";
import { confirmAction } from "../../components/confirmDialog";
import CreateAssetCatalogDialog from "./CreateAssetCatalogDialog.vue";
import LibraryThumbnail from "./LibraryThumbnail.vue";
import AssetCatalogEditor from "./AssetCatalogEditor.vue";
import type { CreateAssetCatalogInput } from "./useAssetLibrary";
import { catalogKey, useAssetLibrary } from "./useAssetLibrary";
import { usePackageDropTarget } from "./usePackageDropTarget";
import { useGuidePage } from "../onboarding/context";
import PlannedFeature from "../../components/PlannedFeature.vue";

const library = useAssetLibrary();
const activeTarget = ref<"dts" | "blender">("dts");
watch(library.targetType, target => { activeTarget.value = target; });
useGuidePage("assets", () => ({
  ready: activeTarget.value === "dts" && library.catalogs.value.length > 0,
  status: activeTarget.value === "dts" ? `当前有 ${library.catalogs.value.length} 个 DTS 资产库` : "Blender 资产库规划中；本步准备 DTS 资产库。",
  loading: library.loading.value,
  busy: library.mutating.value || editorOpen.value || createDialogOpen.value,
  error: library.errorMessage.value,
  refresh: library.refreshTarget,
  actions: [{ label: "导入 DTS 资产包", disabled: !library.canSelectSourceFiles, run: async () => { if (activeTarget.value !== "dts") await setTarget("dts"); await importCatalog(); } }],
  authorAction: { label: "VFA Author", disabled: activeTarget.value !== "dts", run: library.exportAuthoringKit },
}));
const query = ref("");
const selectedAsset = ref<AssetCatalogAssetInfo | null>(null);
const editorOpen = ref(false);
const createDialogOpen = ref(false);
const catalogIndexOpen = ref(true);
const catalogQuery = ref("");
const packageDrop = usePackageDropTarget({
  extension: "vfa",
  disabled: () =>
    activeTarget.value !== "dts" || library.mutating.value || editorOpen.value || createDialogOpen.value,
  onDropPaths: async ({ paths, rejectedNames, pathFailures }) => {
    const imported = await library.importCatalogPackagePaths(
      paths,
      rejectedNames,
      pathFailures,
    );
    if (imported) catalogIndexOpen.value = false;
  },
});

const visibleAssets = computed(() => library.assets.value);
const showingCatalogIndex = computed(
  () => library.sortedCatalogs.value.length > 1 && catalogIndexOpen.value,
);
const visibleCatalogs = computed(() => {
  const normalized = catalogQuery.value.trim().toLocaleLowerCase();
  if (!normalized) return library.sortedCatalogs.value;
  return library.sortedCatalogs.value.filter((catalog) =>
    [catalog.display_name, catalog.catalog_id, catalog.provider_id]
      .some((value) => value.toLocaleLowerCase().includes(normalized)),
  );
});
const suggestedCatalogId = computed(() => {
  const prefix = library.targetType.value === "dts" ? "dts_vegetation" : "blender_vegetation";
  const existing = new Set(library.catalogs.value.map((catalog) => catalog.catalog_id));
  if (!existing.has(prefix)) return prefix;
  let index = 2;
  while (existing.has(`${prefix}_${index}`)) index += 1;
  return `${prefix}_${index}`;
});

let queryTimer: ReturnType<typeof setTimeout> | null = null;
watch(query, (value) => {
  if (queryTimer) clearTimeout(queryTimer);
  queryTimer = setTimeout(() => void library.setAssetQuery(value), 250);
});

onMounted(() => void library.initialize());
const scaleLabel = (value: unknown) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "资产默认";
  const scale = value as Record<string, unknown>;
  if (scale.mode === "fixed") return `固定 ${scalePercent(scale.value)}%`;
  if (scale.mode === "uniform_random") {
    return `${scalePercent(scale.min)}%–${scalePercent(scale.max)}%`;
  }
  return "资产默认";
};
const scalePercent = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? Math.round(value * 100)
    : "-";
const metadataRows = (asset: AssetCatalogAssetInfo) =>
  Object.entries(asset.provider_metadata).map(([key, value]) => ({ key, value: String(value) }));

const openEditor = async () => {
  if (!library.catalogDetail.value) return;
  editorOpen.value = true;
};

const saveCatalog = async (document: Parameters<typeof library.saveAssetCatalog>[0]) => {
  const saved = await library.saveAssetCatalog(document);
  if (saved) {
    selectedAsset.value = null;
    editorOpen.value = false;
  }
};

const createCatalog = async (input: CreateAssetCatalogInput) => {
  const created = await library.createAssetCatalog(input);
  if (!created) return;
  catalogIndexOpen.value = false;
  createDialogOpen.value = false;
  editorOpen.value = true;
};

const selectCatalog = async (key: string) => {
  query.value = "";
  await library.selectCatalog(key);
  if (library.selectedCatalog.value) catalogIndexOpen.value = false;
};

const showCatalogIndex = () => {
  query.value = "";
  selectedAsset.value = null;
  catalogIndexOpen.value = true;
};

const setTarget = async (target: "dts" | "blender") => {
  activeTarget.value = target;
  selectedAsset.value = null;
  if (target === "blender") return;
  query.value = "";
  catalogQuery.value = "";
  selectedAsset.value = null;
  catalogIndexOpen.value = true;
  await library.setTarget(target);
};

const importCatalog = async () => {
  const imported = await library.importCatalogPackage();
  if (imported) catalogIndexOpen.value = false;
};

const deleteCatalog = async () => {
  const catalog = library.selectedCatalog.value;
  if (!catalog) return;
  const confirmed = await confirmAction({
    title: "删除资产库？",
    message: `将删除“${catalog.display_name}”。植被搭配不会被级联删除。`,
    confirmLabel: "删除资产库",
    tone: "danger",
  });
  if (!confirmed) return;
  if (await library.deleteSelectedCatalog()) catalogIndexOpen.value = true;
};
</script>

<template>
  <section
    class="library-page asset-library-page package-drop-target"
    @dragenter="packageDrop.onDragEnter"
    @dragover="packageDrop.onDragOver"
    @dragleave="packageDrop.onDragLeave"
    @drop="packageDrop.onDrop"
  >
    <div v-if="packageDrop.active.value" class="package-drop-overlay" aria-hidden="true">
      <Upload :size="25" />
      <strong>释放以导入资产包</strong>
      <span>支持一个或多个 .vfa 文件</span>
    </div>
    <header class="page-header library-page__header">
      <div>
        <span class="page-header__eyebrow">资源</span>
        <h1>资产库</h1>
        <p>按输出目标管理 Catalog、植被资产、分类与受管缩略图。</p>
      </div>
      <div v-if="activeTarget === 'dts'" class="page-actions">
        <label v-if="showingCatalogIndex" class="search-control">
          <Search :size="17" />
          <span class="sr-only">搜索资产库</span>
          <input v-model="catalogQuery" type="search" placeholder="搜索资产库名称或 ID" />
        </label>
        <label v-else-if="library.selectedCatalog.value" class="search-control">
          <Search :size="17" />
          <span class="sr-only">搜索资产</span>
          <input v-model="query" type="search" placeholder="搜索名称、ID 或标签" />
        </label>
        <button class="icon-button" type="button" title="刷新" @click="library.refreshTarget">
          <RefreshCw :size="17" :class="{ spinning: library.loading.value }" />
        </button>
        <button
          v-if="library.targetType.value === 'dts'"
          class="secondary-button"
          type="button"
          :disabled="library.mutating.value"
          title="导出供 Agent 整理 DTS 资产资料使用的模板与规则"
          @click="library.exportAuthoringKit"
        >
          <PackageOpen :size="17" />VFA Author
        </button>
        <button
          class="secondary-button"
          type="button"
          :disabled="library.mutating.value"
          @click="createDialogOpen = true"
        >
          <Plus :size="17" />新建资产库
        </button>
        <button
          class="primary-button"
          type="button"
          :disabled="library.mutating.value || !library.canSelectSourceFiles"
          title="可选择一个或多个 VerdFlow .vfa 资产包"
          data-guide-anchor="assets"
          @click="importCatalog"
        >
          <Upload :size="17" />导入资产包
        </button>
      </div>
    </header>

    <div class="target-tabs" role="tablist" aria-label="资产目标类型">
      <button type="button" role="tab" :aria-selected="activeTarget === 'dts'" :class="{ active: activeTarget === 'dts' }" @click="setTarget('dts')">
        <Layers3 :size="16" />DTS
      </button>
      <button type="button" role="tab" class="target-tab--planned" :aria-selected="activeTarget === 'blender'" :class="{ active: activeTarget === 'blender' }" @click="setTarget('blender')">
        <Box :size="16" />Blender
      </button>
    </div>

    <PlannedFeature v-if="activeTarget === 'blender'"
      title="Blender 资产库规划中"
      description="Blender 场景的资产管理与种植接入正在规划，当前版本暂未开放。"
      note="目前可在 DTS 工作台中使用 Blender 处理 FBX 模型。"
    >
      <button class="secondary-button" type="button" @click="setTarget('dts')"><ArrowLeft :size="16" />返回 DTS 资产库</button>
    </PlannedFeature>

    <div v-if="activeTarget === 'dts' && library.errorMessage.value" class="inline-alert library-alert" role="alert">
      <div><strong>资产库暂时不可用</strong><p>{{ library.errorMessage.value }}</p></div>
      <button type="button" @click="library.refreshTarget">重新读取</button>
    </div>
    <div v-if="activeTarget === 'dts' && library.noticeMessage.value" class="inline-alert inline-alert--success library-alert" role="status">
      <div><strong>资产包操作完成</strong><p>{{ library.noticeMessage.value }}</p></div>
    </div>

    <div v-if="activeTarget === 'dts'" class="library-workspace asset-library-workspace">
      <main class="library-detail asset-library-detail">
        <section v-if="library.loading.value && !library.catalogs.value.length" class="catalog-index">
          <div class="library-loading library-loading--fill">
            <LoaderCircle :size="20" class="spinning" />正在读取资产库
          </div>
        </section>
        <section v-else-if="showingCatalogIndex" class="catalog-index">
          <header class="catalog-index__header">
            <div>
              <span>{{ library.targetType.value === 'dts' ? 'DTS' : 'Blender' }} Catalog</span>
              <h2>选择资产库</h2>
              <p>选择一个资产库进入植被资产与分类管理。</p>
            </div>
            <strong>{{ library.sortedCatalogs.value.length }}</strong>
          </header>
          <div v-if="library.loading.value" class="library-loading library-loading--fill">
            <LoaderCircle :size="20" class="spinning" />正在读取资产库
          </div>
          <div v-else-if="visibleCatalogs.length" class="catalog-index__list">
            <button
              v-for="catalog in visibleCatalogs"
              :key="catalogKey(catalog)"
              type="button"
              @click="selectCatalog(catalogKey(catalog))"
            >
              <span class="catalog-index__icon">
                <Layers3 v-if="catalog.target_type === 'dts'" :size="20" />
                <Box v-else :size="20" />
              </span>
              <span class="catalog-index__identity">
                <strong>{{ catalog.display_name }}</strong>
                <small>{{ catalog.catalog_id }}</small>
              </span>
              <span class="catalog-index__provider">
                <small>资产接入方式</small>
                <strong>{{ catalog.provider_id }}</strong>
              </span>
              <span class="catalog-index__enter">进入资产库 <ChevronRight :size="15" /></span>
            </button>
          </div>
          <div v-else class="library-empty library-empty--detail">
            <Boxes :size="28" />
            <strong>没有匹配的资产库</strong>
            <p>请调整搜索条件。</p>
          </div>
        </section>

        <template v-else-if="library.selectedCatalog.value">
          <header class="asset-catalog-header">
            <button
              v-if="library.sortedCatalogs.value.length > 1"
              class="catalog-back-button"
              type="button"
              title="返回资产库选择"
              @click="showCatalogIndex"
            >
              <ArrowLeft :size="17" />
              <span>返回资产库</span>
            </button>
            <div>
              <span>{{ library.selectedCatalog.value.target_type.toUpperCase() }} Catalog</span>
              <h2>{{ library.selectedCatalog.value.display_name }}</h2>
              <p>{{ library.selectedCatalog.value.catalog_id }}</p>
            </div>
            <div class="asset-catalog-actions">
              <button
                class="secondary-button"
                type="button"
                title="导出当前资产库的固定共享快照"
                :disabled="library.mutating.value"
                @click="library.exportSelectedCatalog"
              >
                <Download :size="15" />导出 .vfa
              </button>
              <button class="secondary-button" type="button" :disabled="library.mutating.value" @click="openEditor">
                <Pencil :size="15" />编辑资产库
              </button>
              <button class="icon-button" type="button" title="删除资产库" :disabled="library.mutating.value" @click="deleteCatalog">
                <Trash2 :size="16" />
              </button>
            </div>
            <div class="asset-catalog-stats">
              <span><small>Provider</small><strong>{{ library.selectedCatalog.value.provider_id }}</strong></span>
              <span><small>资产</small><strong>{{ library.totalAssets.value }}</strong></span>
              <span><small>分类</small><strong>{{ library.collections.value.length }}</strong></span>
            </div>
          </header>

          <div v-if="library.loadingContent.value && !library.assets.value.length" class="library-loading library-loading--fill">
            <LoaderCircle :size="20" class="spinning" />正在读取目录内容
          </div>

          <div v-else class="asset-content">
            <nav v-if="library.collections.value.length" class="collection-filter" aria-label="资产分类">
              <button type="button" :class="{ active: !library.selectedCollectionId.value }" @click="library.setCollection('')">
                全部
              </button>
              <button
                v-for="collection in library.collections.value"
                :key="collection.collection_id"
                type="button"
                :class="{ active: library.selectedCollectionId.value === collection.collection_id }"
                @click="library.setCollection(collection.collection_id)"
              >
                {{ collection.display_name }} <span>{{ collection.asset_count }}</span>
              </button>
            </nav>
            <div v-if="visibleAssets.length" class="asset-grid">
              <button v-for="asset in visibleAssets" :key="asset.asset_id" type="button" @click="selectedAsset = asset">
                <LibraryThumbnail :resource-id="asset.thumbnail_resource_id">
                  <span class="asset-grid__fallback"><Trees :size="23" /></span>
                </LibraryThumbnail>
                <strong>{{ asset.display_name }}</strong>
                <small>{{ asset.asset_id }}</small>
                <div><span v-for="tag in asset.tags.slice(0, 2)" :key="tag">{{ tag }}</span></div>
              </button>
            </div>
            <div v-else class="library-empty library-empty--detail"><Trees :size="28" /><strong>{{ library.assets.value.length ? '没有匹配的资产' : '目录内没有资产' }}</strong></div>
            <footer v-if="library.nextAssetOrdinal.value !== null" class="asset-load-more">
              <span>已加载 {{ library.assets.value.length }} / {{ library.totalAssets.value }}</span>
              <button class="secondary-button" type="button" :disabled="library.loadingMore.value" @click="library.loadMoreAssets">
                <LoaderCircle v-if="library.loadingMore.value" :size="15" class="spinning" />加载更多
              </button>
            </footer>
          </div>

        </template>
        <div v-else class="library-empty library-empty--detail asset-library-empty-state">
          <Boxes :size="30" />
          <strong>暂无 {{ library.targetType.value === 'dts' ? 'DTS' : 'Blender' }} 资产库</strong>
          <p>新建本地资产库，或导入团队共享的 .vfa 资产包。</p>
          <div>
            <button class="primary-button" type="button" @click="createDialogOpen = true">
              <Plus :size="16" />新建资产库
            </button>
            <button class="secondary-button" type="button" :disabled="library.mutating.value || !library.canSelectSourceFiles" title="可选择一个或多个 VerdFlow .vfa 资产包" @click="importCatalog">
              <Upload :size="16" />导入资产包
            </button>
          </div>
        </div>
      </main>
    </div>

    <aside v-if="selectedAsset" class="asset-detail-drawer">
      <header><div><span>植被资产</span><h2>{{ selectedAsset.display_name }}</h2></div><button type="button" title="关闭" @click="selectedAsset = null">关闭</button></header>
      <div class="asset-detail-drawer__body">
        <span class="asset-detail-preview">
          <LibraryThumbnail :resource-id="selectedAsset.thumbnail_resource_id" :alt="selectedAsset.display_name">
            <span><Trees :size="34" /></span>
          </LibraryThumbnail>
        </span>
        <section><small>Asset ID</small><strong>{{ selectedAsset.asset_id }}</strong></section>
        <section><small>建议缩放</small><strong>{{ scaleLabel(selectedAsset.suggested_scale) }}</strong></section>
        <section><small>参考半径</small><strong>{{ selectedAsset.reference_radius_m === null ? '未指定' : `${selectedAsset.reference_radius_m} 米` }}</strong></section>
        <section><small>参考高度</small><strong>{{ selectedAsset.reference_height_m == null ? '未指定' : `${selectedAsset.reference_height_m} 米` }}</strong></section>
        <section v-if="selectedAsset.tags.length"><small>标签</small><div class="library-tags"><span v-for="tag in selectedAsset.tags" :key="tag">{{ tag }}</span></div></section>
        <section><small>Provider Metadata</small><dl><div v-for="row in metadataRows(selectedAsset)" :key="row.key"><dt>{{ row.key }}</dt><dd>{{ row.value }}</dd></div></dl></section>
      </div>
    </aside>

    <AssetCatalogEditor
      v-if="editorOpen && library.catalogDetail.value"
      :catalog="library.catalogDetail.value"
      :busy="library.mutating.value"
      :import-thumbnail="library.importThumbnail"
      @close="editorOpen = false"
      @save="saveCatalog"
    />
    <CreateAssetCatalogDialog
      v-if="createDialogOpen"
      :target-type="library.targetType.value"
      :initial-catalog-id="suggestedCatalogId"
      :busy="library.mutating.value"
      @close="createDialogOpen = false"
      @create="createCatalog"
    />
  </section>
</template>

<style scoped>
.target-tabs .target-tab--planned { color: var(--color-text-faint); }
</style>
