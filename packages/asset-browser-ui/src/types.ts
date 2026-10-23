export interface BrowserCatalog {
  catalog_id: string;
  display_name: string;
  target_type: string;
  provider_id: string;
}
export interface BrowserAsset {
  asset_id: string;
  display_name: string;
  source_catalog_name?: string | null;
}
export interface AssetSearchRequest {
  target_type: string;
  provider_id: string;
  catalog_ids: string[];
  query: string;
  collection_catalog_id?: string;
  collection_id?: string;
  offset: number;
  limit: number;
  revision?: string;
}
export interface AssetSearchPage<T> {
  items: T[];
  collections: { catalog_id: string; source_catalog_name: string; collection_id: string; display_name: string; asset_count: number }[];
  total_count: number;
  offset: number;
  next_offset: number | null;
  revision: string;
  unavailable_catalogs: { catalog_id: string; message: string }[];
}
