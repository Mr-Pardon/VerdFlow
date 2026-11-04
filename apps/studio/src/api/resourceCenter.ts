export type ResourceKind = "assets" | "strategies" | "plans";
export type ResourceLocalState = "available" | "installed" | "local" | "conflict" | "unavailable";
export interface ResourceSelection { resource_id: string; version: string; revision: string }
export interface CenterResource {
  id: string; kind: ResourceKind; name: string; summary: string; author: string;
  platforms: string[]; tags: string[]; version: string; updated_at: string;
  cover: string; local_state: ResourceLocalState;
}
export interface CenterCatalog { resources: CenterResource[]; revision: string; stale: boolean; warning: string }
export interface ResourceRelation { kind: string; name: string; resource_id: string; version: string; url: string; note: string }
export interface CenterVersion {
  version: string; released_at: string; notes: string; bytes: number;
  installable: boolean; local_state: ResourceLocalState; relations: ResourceRelation[];
}
export interface CenterDetail {
  resource: CenterResource; license: string; versions: CenterVersion[]; selected_version: string;
  readme: string; content_base: string; revision: string; warning: string;
}
export interface StrategyUpdate {
  strategy_id: string; resource_id: string; current_version: string; version: string; major_change: boolean;
}
export interface StrategyUpdates { items: StrategyUpdate[]; warning: string }
