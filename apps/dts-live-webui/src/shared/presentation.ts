import {
  type StrategyOption,
  type ProfileSummary,
  type CatalogSummary,
  type AssetInfo,
  type MappingCandidate,
  type AssignmentMap,
} from "../types";

export const profileStorageKey = (strategy: StrategyOption) =>
  `verdflow.dts-live.profile.${strategy.summary.package_hash}`;

export const profileKey = (profile: ProfileSummary) =>
  profile.profile_id;

export const catalogKey = (catalog: CatalogSummary) =>
  catalog.catalog_id;

export const defaultCandidate = (asset: AssetInfo): MappingCandidate => ({
  asset_id: asset.asset_id,
  weight: 1,
  scale: cloneScale(asset.suggested_scale),
});

export const normalizeUniformScale = (scale: AssetInfo["suggested_scale"]) => {
  if (scale?.mode === "uniform_random") return { ...scale };
  const value = scale?.mode === "fixed" ? scale.value : 1;
  return { mode: "uniform_random" as const, min: value, max: value };
};

export const fixedScale = (scale: AssetInfo["suggested_scale"]) =>
  scale?.mode === "fixed" ? scale.value : scale?.min ?? 1;

export const scalePercent = (value: number) => Math.round(value * 100);

export const cloneScale = (scale: AssetInfo["suggested_scale"]): MappingCandidate["scale"] =>
  scale?.mode === "fixed"
    ? { mode: "fixed", value: scale.value }
    : scale?.mode === "uniform_random"
      ? { mode: "uniform_random", min: scale.min, max: scale.max }
      : { mode: "fixed", value: 1 };

export const candidateKey = (roleKey: string, assetId: string) => `${roleKey}\u0000${assetId}`;

export const cloneAssignments = (value: AssignmentMap): AssignmentMap =>
  JSON.parse(JSON.stringify(value)) as AssignmentMap;

export const readableError = (error: unknown) => {
  return error instanceof Error ? error.message : String(error);
};

export const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

export const compareVersions = (left: string, right: string) => {
  const leftParts = left.match(/\d+/g)?.map(Number) ?? [];
  const rightParts = right.match(/\d+/g)?.map(Number) ?? [];
  for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index += 1) {
    const difference = (leftParts[index] ?? 0) - (rightParts[index] ?? 0);
    if (difference) return difference;
  }
  return left.localeCompare(right);
};
