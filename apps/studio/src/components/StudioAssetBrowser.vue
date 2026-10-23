<script setup lang="ts">
import { computed } from "vue";
import { Trees } from "@lucide/vue";
import { AssetBrowser } from "@verdflow/asset-browser-ui";
import type { AssetSearchRequest } from "@verdflow/asset-browser-ui";
import { LocalApiClient } from "../api";
import type { AssetCatalogAssetInfo, AssetCatalogSummary } from "../api";
import { platformBridge } from "../platform";
import LibraryThumbnail from "../features/library/LibraryThumbnail.vue";
import { bindingBrowserAssets, type RoleMappingDraft } from "../features/shared/roleMapping";

const props = defineProps<{ catalogs: AssetCatalogSummary[]; mappings: RoleMappingDraft; selectedIds: string[]; disabled?: boolean }>();
const emit = defineEmits<{ toggle: [asset: AssetCatalogAssetInfo] }>();
const client = new LocalApiClient(platformBridge());
const candidates = computed(() => Object.values(props.mappings).flat());
const target = computed(() => candidates.value.find(c => c.bindingCandidate?.target_asset_ref)?.bindingCandidate?.target_asset_ref as { target_type: string; provider_id: string } | undefined);
const sources = computed(() => candidates.value.flatMap(c => {
  const link = c.bindingCandidate?.catalog_link as { catalog_id: string } | undefined;
  return link ? [link.catalog_id] : [];
}));
const search = (request: AssetSearchRequest) => client.searchAssets(request);
const mapAsset = (asset: AssetCatalogAssetInfo) => bindingBrowserAssets([asset], props.mappings)[0];
const assetLabel = (asset: AssetCatalogAssetInfo) => String((asset.binding_candidate?.catalog_link as { asset_id?: string } | undefined)?.asset_id ?? asset.asset_id);
</script>

<template>
  <AssetBrowser :catalogs="catalogs" :target-type="target?.target_type ?? catalogs[0]?.target_type ?? 'dts'" :provider-id="target?.provider_id"
    :initial-catalog-ids="sources" :selected-ids="selectedIds" :search="search" :map-asset="mapAsset" :asset-label="assetLabel" :disabled="disabled" @toggle="emit('toggle', $event)">
    <template #thumbnail="{ asset }"><LibraryThumbnail :resource-id="asset.thumbnail_resource_id" :alt="asset.display_name"><Trees :size="28" /></LibraryThumbnail></template>
  </AssetBrowser>
</template>
