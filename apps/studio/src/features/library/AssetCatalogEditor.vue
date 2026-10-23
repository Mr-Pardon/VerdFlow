<script setup lang="ts">
import {
  FolderPlus,
  ListFilter,
  ImagePlus,
  Plus,
  Save,
  Search,
  Trash2,
  Trees,
  X,
} from "@lucide/vue";
import { computed, ref, watch } from "vue";

import type {
  AssetCatalogDocument,
  AssetCatalogDocumentAsset,
  AssetCatalogDetail,
} from "../../api";
import LibraryThumbnail from "./LibraryThumbnail.vue";

const props = defineProps<{
  catalog: AssetCatalogDetail;
  busy: boolean;
  importThumbnail: () => Promise<string | null>;
}>();

const emit = defineEmits<{
  close: [];
  save: [document: AssetCatalogDocument];
}>();

const draft = ref(cloneDocument(props.catalog.document));
const selectedAssetIndex = ref(0);
const assetSearch = ref("");
const collectionFilter = ref("");

watch(
  () => props.catalog,
  (value) => {
    draft.value = cloneDocument(value.document);
    selectedAssetIndex.value = Math.min(
      selectedAssetIndex.value,
      Math.max(0, draft.value.assets.length - 1),
    );
  },
);

const selectedAsset = computed(() => draft.value.assets[selectedAssetIndex.value] ?? null);
const collections = computed(() => draft.value.collections ?? (draft.value.collections = []));
const visibleAssetRows = computed(() => {
  const needle = assetSearch.value.trim().toLocaleLowerCase();
  const collection = collectionFilter.value
    ? collections.value.find((item) => item.collection_id === collectionFilter.value)
    : null;
  return draft.value.assets
    .map((asset, index) => ({ asset, index }))
    .filter(({ asset }) => {
      const matchesCollection = !collection || collection.asset_ids.includes(asset.asset_id);
      const matchesSearch = !needle || [
        asset.display_name,
        asset.asset_id,
        asset.provider_metadata.model_name,
      ].some((value) => String(value ?? "").toLocaleLowerCase().includes(needle));
      return matchesCollection && matchesSearch;
    });
});
const selectedAssetHidden = computed(() =>
  Boolean(selectedAsset.value) &&
  !visibleAssetRows.value.some(({ index }) => index === selectedAssetIndex.value),
);
const canSave = computed(
  () => {
    const assetIds = draft.value.assets.map((asset) => asset.asset_id.trim());
    const collectionIds = collections.value.map((collection) => collection.collection_id.trim());
    return (
      Boolean(draft.value.display_name?.trim()) &&
      assetIds.length > 0 &&
      new Set(assetIds).size === assetIds.length &&
      draft.value.assets.every(
        (asset) =>
          asset.asset_id.trim() &&
          asset.display_name.trim() &&
          validScale(asset) &&
          validReferenceDimensions(asset),
      ) &&
      new Set(collectionIds).size === collectionIds.length &&
      collections.value.every(
        (collection) =>
          collection.collection_id.trim() &&
          collection.display_name.trim() &&
          collection.asset_ids.length > 0,
      )
    );
  },
);

const addAsset = () => {
  const assetId = uniqueId("asset", draft.value.assets.map((asset) => asset.asset_id));
  draft.value.assets.push({
    asset_id: assetId,
    display_name: "新植被资产",
    provider_metadata: { model_name: assetId },
    tags: [],
  });
  assetSearch.value = "";
  collectionFilter.value = "";
  selectedAssetIndex.value = draft.value.assets.length - 1;
};

const removeAsset = (index: number) => {
  const [removed] = draft.value.assets.splice(index, 1);
  if (!removed) return;
  for (const collection of collections.value) {
    collection.asset_ids = collection.asset_ids.filter((id) => id !== removed.asset_id);
  }
  selectedAssetIndex.value = Math.min(index, Math.max(0, draft.value.assets.length - 1));
};

const updateAssetId = (asset: AssetCatalogDocumentAsset, nextValue: string) => {
  const previous = asset.asset_id;
  asset.asset_id = nextValue;
  for (const collection of collections.value) {
    collection.asset_ids = collection.asset_ids.map((id) =>
      id === previous ? nextValue : id,
    );
  }
};

const addCollection = () => {
  const collectionId = uniqueId(
    "collection",
    collections.value.map((collection) => collection.collection_id),
  );
  collections.value.push({
    collection_id: collectionId,
    display_name: "新分类",
    asset_ids: [],
  });
};

const removeCollection = (index: number) => collections.value.splice(index, 1);

const toggleMembership = (collectionId: string, enabled: boolean) => {
  const asset = selectedAsset.value;
  const collection = collections.value.find((item) => item.collection_id === collectionId);
  if (!asset || !collection) return;
  collection.asset_ids = enabled
    ? [...new Set([...collection.asset_ids, asset.asset_id])]
    : collection.asset_ids.filter((id) => id !== asset.asset_id);
};

const setTags = (value: string) => {
  if (!selectedAsset.value) return;
  selectedAsset.value.tags = value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
};

const setScaleMode = (mode: string) => {
  if (!selectedAsset.value) return;
  if (mode === "none") delete selectedAsset.value.suggested_scale;
  else if (mode === "fixed") selectedAsset.value.suggested_scale = { mode, value: 1 };
  else selectedAsset.value.suggested_scale = { mode: "uniform_random", min: 0.8, max: 1.2 };
};

const setReferenceDimension = (field: "reference_radius_m" | "reference_height_m", value: string) => {
  if (!selectedAsset.value) return;
  if (!value.trim()) {
    delete selectedAsset.value[field];
    return;
  }
  selectedAsset.value[field] = Number(value);
};

const setScalePercent = (
  field: "value" | "min" | "max",
  raw: string,
) => {
  const scale = selectedAsset.value?.suggested_scale;
  const value = Number(raw) / 100;
  if (!scale || !Number.isFinite(value) || value <= 0) return;
  if (scale.mode === "fixed" && field === "value") scale.value = value;
  if (scale.mode === "uniform_random" && field !== "value") {
    scale[field] = value;
  }
};

const scalePercent = (value: number) => Math.round(value * 100);

const chooseThumbnail = async () => {
  if (!selectedAsset.value) return;
  const reference = await props.importThumbnail();
  if (reference) selectedAsset.value.thumbnail = reference;
};

const showSelectedAsset = () => {
  assetSearch.value = "";
  collectionFilter.value = "";
};

const thumbnailResourceId = (asset: AssetCatalogDocumentAsset | null) => {
  const prefix = "verdflow-resource:";
  return asset?.thumbnail?.startsWith(prefix) ? asset.thumbnail.slice(prefix.length) : null;
};

function cloneDocument(document: AssetCatalogDocument): AssetCatalogDocument {
  return JSON.parse(JSON.stringify(document)) as AssetCatalogDocument;
}

function uniqueId(prefix: string, existing: string[]) {
  let index = 1;
  while (existing.includes(`${prefix}_${index}`)) index += 1;
  return `${prefix}_${index}`;
}

function validScale(asset: AssetCatalogDocumentAsset) {
  const scale = asset.suggested_scale;
  if (!scale) return true;
  if (scale.mode === "fixed") return Number.isFinite(scale.value) && scale.value > 0;
  return (
    Number.isFinite(scale.min) &&
    Number.isFinite(scale.max) &&
    scale.min > 0 &&
    scale.max >= scale.min
  );
}

function validReferenceDimensions(asset: AssetCatalogDocumentAsset) {
  return [asset.reference_radius_m, asset.reference_height_m].every(
    value => value === undefined || (Number.isFinite(value) && value > 0),
  );
}
</script>

<template>
  <div class="dialog-backdrop asset-catalog-backdrop">
    <section class="asset-catalog-editor" role="dialog" aria-modal="true" aria-label="编辑资产库">
      <header>
        <div>
          <span>资产库</span>
          <h2>编辑 {{ draft.display_name }}</h2>
          <p>保存后立即用于本机检索与新建映射；既有映射不会被静默改写。</p>
        </div>
        <button class="icon-button" type="button" title="关闭" :disabled="busy" @click="emit('close')"><X :size="18" /></button>
      </header>

      <div class="asset-catalog-editor__identity">
        <label><span>资产库名称</span><input v-model="draft.display_name" type="text" /></label>
        <span><small>Catalog ID</small><strong>{{ draft.catalog_id }}</strong></span>
        <span><small>Provider</small><strong>{{ draft.provider_id }}</strong></span>
      </div>

      <div class="asset-catalog-editor__body">
        <aside class="catalog-editor-collections">
          <header><strong>分类</strong><button type="button" title="新增分类" @click="addCollection"><FolderPlus :size="16" /></button></header>
          <div v-if="collections.length">
            <label v-for="(collection, index) in collections" :key="collection.collection_id">
              <input v-model="collection.display_name" type="text" aria-label="分类名称" />
              <small :class="{ invalid: collection.asset_ids.length === 0 }">{{ collection.asset_ids.length }}</small>
              <button type="button" title="删除分类" @click="removeCollection(index)"><Trash2 :size="14" /></button>
            </label>
          </div>
          <p v-else>尚未建立分类。</p>
        </aside>

        <section class="catalog-editor-assets">
          <header><strong>植被资产</strong><button type="button" @click="addAsset"><Plus :size="15" />新增</button></header>
          <div class="catalog-editor-assets__filters">
            <label class="catalog-editor-assets__search">
              <Search :size="14" />
              <span class="sr-only">搜索植被资产</span>
              <input v-model="assetSearch" type="search" placeholder="名称、目标资产或 ID" />
            </label>
            <label class="catalog-editor-assets__collection">
              <ListFilter :size="14" />
              <span class="sr-only">按分类筛选</span>
              <select v-model="collectionFilter">
                <option value="">全部分类</option>
                <option
                  v-for="collection in collections"
                  :key="collection.collection_id"
                  :value="collection.collection_id"
                >
                  {{ collection.display_name }}
                </option>
              </select>
            </label>
            <div class="catalog-editor-assets__filter-status">
              <span>{{ visibleAssetRows.length }} / {{ draft.assets.length }}</span>
              <button v-if="selectedAssetHidden" type="button" @click="showSelectedAsset">
                显示当前资产
              </button>
            </div>
          </div>
          <div v-if="visibleAssetRows.length" class="catalog-editor-assets__list">
            <button
              v-for="({ asset, index }) in visibleAssetRows"
              :key="`${asset.asset_id}:${index}`"
              type="button"
              :class="{ active: selectedAssetIndex === index }"
              @click="selectedAssetIndex = index"
            >
              <span class="catalog-editor-asset-list-thumbnail">
                <LibraryThumbnail :resource-id="thumbnailResourceId(asset)" :alt="asset.display_name">
                  <span><Trees :size="15" /></span>
                </LibraryThumbnail>
              </span>
              <span><strong>{{ asset.display_name }}</strong><small>{{ asset.asset_id }}</small></span>
            </button>
          </div>
          <div v-else class="catalog-editor-assets__empty">
            <Search :size="20" />
            <span>没有匹配的植被资产</span>
          </div>
        </section>

        <section v-if="selectedAsset" class="catalog-editor-asset-form">
          <header>
            <div><span>资产详情</span><strong>{{ selectedAsset.display_name }}</strong></div>
            <button type="button" title="删除资产" :disabled="draft.assets.length <= 1" @click="removeAsset(selectedAssetIndex)"><Trash2 :size="16" /></button>
          </header>
          <div class="catalog-editor-asset-form__content">
            <div class="catalog-editor-thumbnail">
              <LibraryThumbnail :resource-id="thumbnailResourceId(selectedAsset)"><span><Trees :size="28" /></span></LibraryThumbnail>
              <button type="button" :disabled="busy" @click="chooseThumbnail"><ImagePlus :size="15" />选择缩略图</button>
            </div>
            <div class="catalog-editor-form-grid">
              <label><span>植被名称</span><input v-model="selectedAsset.display_name" type="text" /></label>
              <label><span>Asset ID</span><input :value="selectedAsset.asset_id" type="text" @input="updateAssetId(selectedAsset, ($event.target as HTMLInputElement).value)" /></label>
              <label class="catalog-editor-form-grid__wide"><span>目标静态网格体名称</span><input v-model="selectedAsset.provider_metadata.model_name" type="text" /></label>
              <label class="catalog-editor-form-grid__wide"><span>标签 <small>使用英文逗号分隔</small></span><input :value="(selectedAsset.tags ?? []).join(', ')" type="text" @input="setTags(($event.target as HTMLInputElement).value)" /></label>
              <label><span>建议缩放</span><select :value="selectedAsset.suggested_scale?.mode ?? 'none'" @change="setScaleMode(($event.target as HTMLSelectElement).value)"><option value="none">不指定</option><option value="fixed">固定</option><option value="uniform_random">随机范围</option></select></label>
              <label><span>参考半径（米）</span><input :value="selectedAsset.reference_radius_m ?? ''" min="0.01" step="0.1" type="number" placeholder="未指定" @input="setReferenceDimension('reference_radius_m', ($event.target as HTMLInputElement).value)" /></label>
              <label><span>参考高度（米）</span><input :value="selectedAsset.reference_height_m ?? ''" min="0.01" step="0.1" type="number" placeholder="未指定" @input="setReferenceDimension('reference_height_m', ($event.target as HTMLInputElement).value)" /></label>
              <template v-if="selectedAsset.suggested_scale?.mode === 'fixed'"><label><span>缩放值 (%)</span><input :value="scalePercent(selectedAsset.suggested_scale.value)" min="1" step="1" type="number" @input="setScalePercent('value', ($event.target as HTMLInputElement).value)" /></label></template>
              <template v-else-if="selectedAsset.suggested_scale?.mode === 'uniform_random'"><label><span>最小 (%)</span><input :value="scalePercent(selectedAsset.suggested_scale.min)" min="1" step="1" type="number" @input="setScalePercent('min', ($event.target as HTMLInputElement).value)" /></label><label><span>最大 (%)</span><input :value="scalePercent(selectedAsset.suggested_scale.max)" min="1" step="1" type="number" @input="setScalePercent('max', ($event.target as HTMLInputElement).value)" /></label></template>
            </div>
            <fieldset v-if="collections.length" class="catalog-editor-memberships">
              <legend>所属分类</legend>
              <div class="catalog-editor-memberships__options">
                <label
                  v-for="collection in collections"
                  :key="collection.collection_id"
                  :class="{ active: collection.asset_ids.includes(selectedAsset.asset_id) }"
                  :title="collection.display_name"
                >
                  <input
                    type="checkbox"
                    :checked="collection.asset_ids.includes(selectedAsset.asset_id)"
                    @change="toggleMembership(collection.collection_id, ($event.target as HTMLInputElement).checked)"
                  />
                  <span>{{ collection.display_name }}</span>
                </label>
              </div>
            </fieldset>
          </div>
        </section>
      </div>

      <footer>
        <div class="catalog-editor-destructive-action"><button type="button" :disabled="busy" @click="emit('close')">取消</button></div>
        <div class="catalog-editor-primary-actions">
          <span v-if="!canSave" class="catalog-editor-validation-hint">请补全资产信息，并确保每个分类至少包含一个资产。</span>
          <button class="primary-button" type="button" :disabled="busy || !canSave" @click="emit('save', cloneDocument(draft))"><Save :size="15" />保存资产库</button>
        </div>
      </footer>
    </section>
  </div>
</template>
