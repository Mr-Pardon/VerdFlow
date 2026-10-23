import { onBeforeUnmount, shallowRef } from "vue";

import { LocalApiClient } from "../../api";
import type {
  AssetCatalogAssetInfo,
  AssetCatalogSummary,
  RoleBindingProfileSummary,
  StrategyLibraryDetail,
} from "../../api";
import { platformBridge } from "../../platform";
import { parseAssignments, parseRoleEnabled } from "../shared/roleMapping";

export interface PlantingProfilePreview {
  plants: Array<{ assetId: string; name: string; resourceId: string | null }>;
  roleCount: number;
}

export const usePlantingChoicePreviews = () => {
  const client = new LocalApiClient(platformBridge());
  const strategyDetails = shallowRef<Record<string, StrategyLibraryDetail>>({});
  const profilePreviews = shallowRef<Record<string, PlantingProfilePreview>>(
    {},
  );
  const pendingStrategies = new Set<string>();
  const pendingProfiles = new Set<string>();
  const previewGenerations = new Map<string, number>();
  let disposed = false;
  onBeforeUnmount(() => {
    disposed = true;
  });

  const loadStrategies = async (hashes: string[]) => {
    for (const hash of hashes) {
      if (disposed) return;
      if (strategyDetails.value[hash] || pendingStrategies.has(hash)) continue;
      pendingStrategies.add(hash);
      try {
        const detail = await client.strategy(hash);
        if (!disposed)
          strategyDetails.value = { ...strategyDetails.value, [hash]: detail };
      } catch {
        // Optional card previews must not block selecting or executing a strategy.
      } finally {
        pendingStrategies.delete(hash);
      }
    }
  };

  const loadProfiles = async (
    profiles: RoleBindingProfileSummary[],
    _catalogs: AssetCatalogSummary[],
  ) => {
    for (const profile of profiles) {
      if (disposed) return;
      if (profile.plants) {
        profilePreviews.value = { ...profilePreviews.value, [profile.profile_id]: {
          plants: profile.plants.map(plant => ({ assetId: plant.asset_id, name: plant.display_name, resourceId: plant.thumbnail_resource_id })),
          roleCount: profile.enabled_role_count ?? 0,
        } };
        continue;
      }
      const requestKey = `${profile.profile_id}:${profile.generation}`;
      if (
        previewGenerations.get(profile.profile_id) === profile.generation ||
        pendingProfiles.has(requestKey)
      )
        continue;
      pendingProfiles.add(requestKey);
      try {
        const detail = await client.roleBindingProfile(profile.profile_id);
        const assets: AssetCatalogAssetInfo[] = detail.assets ?? [];
        const assetById = new Map(
          assets.map((asset) => [asset.asset_id, asset]),
        );
        const enabled = parseRoleEnabled(detail.assignments);
        const mappings = parseAssignments(detail.assignments, assets);
        const plants = new Map<
          string,
          PlantingProfilePreview["plants"][number]
        >();
        let roleCount = 0;
        for (const [key, candidates] of Object.entries(mappings)) {
          if (enabled[key] === false || !candidates.length) continue;
          roleCount += 1;
          for (const candidate of candidates) {
            const asset = assetById.get(candidate.assetId);
            plants.set(candidate.assetId, {
              assetId: candidate.assetId,
              name: asset?.display_name || candidate.displayNameSnapshot,
              resourceId: asset?.thumbnail_resource_id ?? null,
            });
          }
        }
        if (
          !disposed &&
          (previewGenerations.get(profile.profile_id) ?? -1) <=
            detail.summary.generation
        ) {
          previewGenerations.set(profile.profile_id, detail.summary.generation);
          profilePreviews.value = {
            ...profilePreviews.value,
            [profile.profile_id]: { plants: [...plants.values()], roleCount },
          };
        }
      } catch {
        // Missing preview metadata does not invalidate a self-contained profile.
      } finally {
        pendingProfiles.delete(requestKey);
      }
    }
  };

  return { strategyDetails, profilePreviews, loadStrategies, loadProfiles };
};
