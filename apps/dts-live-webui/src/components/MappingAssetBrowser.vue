<script setup lang="ts">
import { computed } from "vue";
import { AssetBrowser } from "@verdflow/asset-browser-ui";
import VegetationThumbnail from "./VegetationThumbnail.vue";
import type { AssetInfo } from "../types";
import type { LiveConfiguration } from "../features/useLiveConfiguration";

const props = defineProps<{ controller: LiveConfiguration }>();
const { options, assignments, activeCandidates, savingProfile, toggleAsset, thumbnailFor, searchAssets } = props.controller;
const candidates = computed(() => Object.values(assignments.value).flatMap(item => item.candidates));
const target = computed(() => candidates.value.find(item => item.target_asset_ref)?.target_asset_ref);
const sources = computed(() => candidates.value.flatMap(item => item.catalog_link ? [item.catalog_link.catalog_id] : []));
const mapAsset = (asset: AssetInfo): AssetInfo => {
  const seed = asset.binding_candidate;
  if (!seed) return asset;
  const existing = candidates.value.find(item => item.catalog_link && seed.catalog_link &&
    item.catalog_link.catalog_id === seed.catalog_link.catalog_id && item.catalog_link.asset_id === seed.catalog_link.asset_id);
  return { ...asset, asset_id: existing?.asset_id ?? seed.asset_id, binding_candidate: existing ?? seed };
};
const assetLabel = (asset: AssetInfo) => asset.binding_candidate?.catalog_link?.asset_id ?? asset.asset_id;
</script>

<template>
  <AssetBrowser class="asset-picker" :catalogs="options.catalogs" :provider-id="target?.provider_id" :initial-catalog-ids="sources"
    :selected-ids="activeCandidates.map(item => item.asset_id)" :search="searchAssets" :map-asset="mapAsset" :asset-label="assetLabel"
    :disabled="savingProfile" @toggle="toggleAsset">
    <template #thumbnail="{ asset }"><VegetationThumbnail :src="thumbnailFor(asset)" /></template>
  </AssetBrowser>
</template>
