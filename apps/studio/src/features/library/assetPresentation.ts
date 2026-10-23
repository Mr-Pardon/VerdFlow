import { LocalApiClient } from "../../api";
import type { AssetCatalogAssetInfo } from "../../api";

export const enrichAssetThumbnails = async (
  client: LocalApiClient,
  assets: AssetCatalogAssetInfo[],
  input: { targetType: string; catalogId: string },
): Promise<AssetCatalogAssetInfo[]> => {
  const missing = new Set(
    assets
      .filter((asset) => !asset.thumbnail_resource_id)
      .map((asset) => asset.asset_id),
  );
  if (!missing.size) return assets;

  const current = await loadCatalogAssets(
    client,
    input.targetType,
    input.catalogId,
  ).catch(() => []);
  const thumbnails = new Map(
    current
      .filter((asset) => missing.has(asset.asset_id) && asset.thumbnail_resource_id)
      .map((asset) => [asset.asset_id, asset.thumbnail_resource_id] as const),
  );
  return assets.map((asset) => {
    const thumbnail = thumbnails.get(asset.asset_id);
    return thumbnail ? { ...asset, thumbnail_resource_id: thumbnail } : asset;
  });
};

const loadCatalogAssets = async (
  client: LocalApiClient,
  targetType: string,
  catalogId: string,
) => {
  const assets: AssetCatalogAssetInfo[] = [];
  let afterOrdinal = -1;
  do {
    const page = await client.assetCatalogAssets({
      targetType,
      catalogId,
      afterOrdinal,
      limit: 1000,
    });
    assets.push(...page.items);
    if (page.next_after_ordinal === null) return assets;
    afterOrdinal = page.next_after_ordinal;
  } while (true);
};
