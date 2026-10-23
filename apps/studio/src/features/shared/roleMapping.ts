import type { AssetCatalogAssetInfo } from "../../api";

export type MappingScaleMode = "catalog" | "fixed" | "uniform_random";

export interface MappingCandidateDraft {
  bindingCandidate?: Record<string, unknown>;
  asset?: AssetCatalogAssetInfo;
  assetId: string;
  displayNameSnapshot: string;
  weight: number;
  scaleMode: MappingScaleMode;
  fixedScale: number;
  minScale: number;
  maxScale: number;
}

export type AssignmentMap = Record<string, Record<string, unknown>>;
export type RoleMappingDraft = Record<string, MappingCandidateDraft[]>;
export type RoleEnabledMap = Record<string, boolean>;
const WEIGHT_TOTAL = 100;

export const hasRepeatedTarget = (candidates: MappingCandidateDraft[]): boolean => {
  const hashes = candidates.flatMap((candidate) => {
    const link = candidate.bindingCandidate?.catalog_link as { observed_target_hash?: string } | undefined;
    return link?.observed_target_hash ? [link.observed_target_hash] : [];
  });
  return new Set(hashes).size < hashes.length;
};

export const balanceCandidateWeights = (
  candidates: MappingCandidateDraft[],
  preferredAssetId?: string,
  preferredWeight?: number,
): MappingCandidateDraft[] => {
  if (!candidates.length) return [];
  if (candidates.length === 1) return [{ ...candidates[0], weight: 1 }];

  const preferredIndex = preferredAssetId
    ? candidates.findIndex((candidate) => candidate.assetId === preferredAssetId)
    : -1;
  const rawWeights = candidates.map((candidate) =>
    Number.isFinite(candidate.weight) && candidate.weight > 0 ? candidate.weight : 1,
  );

  let units: number[];
  if (preferredIndex >= 0 && Number.isFinite(preferredWeight)) {
    const preferredUnits = Math.min(
      WEIGHT_TOTAL - (candidates.length - 1),
      Math.max(1, Math.round((preferredWeight ?? 0.01) * WEIGHT_TOTAL)),
    );
    const otherIndexes = candidates.flatMap((_, index) => index === preferredIndex ? [] : [index]);
    const otherUnits = allocateUnits(
      otherIndexes.map((index) => rawWeights[index]),
      WEIGHT_TOTAL - preferredUnits,
    );
    units = candidates.map((_, index) => index === preferredIndex
      ? preferredUnits
      : otherUnits[otherIndexes.indexOf(index)]);
  } else {
    units = allocateUnits(rawWeights, WEIGHT_TOTAL);
  }
  return candidates.map((candidate, index) => ({
    ...candidate,
    weight: units[index] / WEIGHT_TOTAL,
  }));
};

export const weightPercentages = (weights: number[]): number[] => {
  if (!weights.length) return [];
  if (weights.length === 1) return [WEIGHT_TOTAL];
  return allocateUnits(weights, WEIGHT_TOTAL);
};
export const parseRoleEnabled = (
  assignments: AssignmentMap, roles: readonly { role_key: string }[] = [],
): RoleEnabledMap => ({
  ...Object.fromEntries(roles.map((role) => [role.role_key, false])),
  ...Object.fromEntries(Object.entries(assignments).map(([key, value]) => [key, value.enabled !== false])),
});

export const parseAssignments = (
  assignments: AssignmentMap,
  assets: AssetCatalogAssetInfo[],
): RoleMappingDraft => {
  const result: RoleMappingDraft = {};
  for (const [roleKey, assignment] of Object.entries(assignments)) {
    if (!Array.isArray(assignment.candidates)) continue;
    result[roleKey] = balanceCandidateWeights(assignment.candidates.flatMap((raw) => {
      if (!raw || typeof raw !== "object") return [];
      const candidate = raw as Record<string, unknown>;
      if (typeof candidate.asset_id !== "string") return [];
      const asset = assets.find((item) => item.asset_id === candidate.asset_id);
      return [parseCandidate(candidate, asset)];
    }));
  }
  return result;
};

export const defaultCandidate = (
  assetId: string,
  asset?: AssetCatalogAssetInfo,
): MappingCandidateDraft => {
  const suggested = isRecord(asset?.suggested_scale) ? asset.suggested_scale : null;
  const draft: MappingCandidateDraft = {
    bindingCandidate: asset?.binding_candidate ? { ...asset.binding_candidate } : undefined,
    asset,
    assetId,
    displayNameSnapshot: asset?.display_name ?? assetId,
    weight: 1,
    scaleMode: "catalog",
    fixedScale: 1,
    minScale: 0.8,
    maxScale: 1.2,
  };
  if (suggested?.mode === "fixed" && positiveNumber(suggested.value)) {
    draft.scaleMode = "fixed";
    draft.fixedScale = suggested.value;
  } else if (
    suggested?.mode === "uniform_random" &&
    positiveNumber(suggested.min) &&
    positiveNumber(suggested.max)
  ) {
    draft.scaleMode = "uniform_random";
    draft.minScale = suggested.min;
    draft.maxScale = suggested.max;
  }
  return draft;
};

export const serializeMappings = (mappings: RoleMappingDraft, enabled: RoleEnabledMap = {}): AssignmentMap => {
  const assignments: AssignmentMap = {};
  for (const roleKey of new Set([...Object.keys(mappings), ...Object.keys(enabled)])) {
    const candidates = balanceCandidateWeights(mappings[roleKey] ?? []);
    if (enabled[roleKey] !== false && !validCandidates(candidates)) continue;
    assignments[roleKey] = {
      enabled: enabled[roleKey] !== false,
      candidates: candidates.map((candidate) => {
        const value: Record<string, unknown> = {
          ...candidate.bindingCandidate,
          asset_id: candidate.assetId,
          weight: candidate.weight,
        };
        if (candidate.scaleMode === "fixed") {
          value.scale = { mode: "fixed", value: candidate.fixedScale };
        } else if (candidate.scaleMode === "uniform_random") {
          value.scale = {
            mode: "uniform_random",
            min: candidate.minScale,
            max: candidate.maxScale,
          };
        } else if (candidate.bindingCandidate) {
          value.scale = candidate.asset?.suggested_scale ?? { mode: "fixed", value: 1 };
        }
        return value;
      }),
    };
  }
  return assignments;
};

export const validCandidates = (candidates?: MappingCandidateDraft[]) =>
  Boolean(
    candidates?.length &&
      candidates.every(
        (candidate) =>
          candidate.assetId &&
          positiveNumber(candidate.weight) &&
          (candidate.scaleMode === "catalog" ||
            (candidate.scaleMode === "fixed" && positiveNumber(candidate.fixedScale)) ||
            (candidate.scaleMode === "uniform_random" &&
              positiveNumber(candidate.minScale) &&
              positiveNumber(candidate.maxScale) &&
              candidate.minScale <= candidate.maxScale)),
      ),
  );

export const canonicalAssignments = (assignments: AssignmentMap) =>
  JSON.stringify(
    Object.fromEntries(
      Object.entries(assignments)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([roleKey, assignment]) => [roleKey, assignment]),
    ),
  );

const parseCandidate = (
  candidate: Record<string, unknown>,
  asset?: AssetCatalogAssetInfo,
): MappingCandidateDraft => {
  const fallback = defaultCandidate(String(candidate.asset_id), asset);
  if (isRecord(candidate.target_asset_ref)) fallback.bindingCandidate = { ...candidate };
  if (typeof candidate.display_name_snapshot === "string") {
    fallback.displayNameSnapshot = candidate.display_name_snapshot;
  }
  const scale = isRecord(candidate.scale) ? candidate.scale : null;
  if (scale?.mode === "fixed" && positiveNumber(scale.value)) {
    fallback.scaleMode = "fixed";
    fallback.fixedScale = scale.value;
  } else if (
    scale?.mode === "uniform_random" &&
    positiveNumber(scale.min) &&
    positiveNumber(scale.max)
  ) {
    fallback.scaleMode = "uniform_random";
    fallback.minScale = scale.min;
    fallback.maxScale = scale.max;
  }
  fallback.weight = positiveNumber(candidate.weight) ? candidate.weight : 1;
  return fallback;
};

export const bindingBrowserAssets = (
  assets: AssetCatalogAssetInfo[], mappings: RoleMappingDraft,
): AssetCatalogAssetInfo[] => {
  const selected = Object.values(mappings).flat();
  return assets.map((asset) => {
    const seed = asset.binding_candidate;
    if (!seed) return asset;
    const link = seed.catalog_link as { catalog_id: string; asset_id: string } | undefined;
    const existing = selected.find((candidate) => {
      const previous = candidate.bindingCandidate?.catalog_link as typeof link;
      return link && previous?.catalog_id === link.catalog_id && previous.asset_id === link.asset_id;
    });
    return { ...asset, asset_id: existing?.assetId ?? String(seed.asset_id),
      binding_candidate: existing?.bindingCandidate ?? seed };
  });
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));

const positiveNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;

const allocateUnits = (weights: number[], totalUnits: number): number[] => {
  const positive = weights.map((weight) => Number.isFinite(weight) && weight > 0 ? weight : 1);
  const total = positive.reduce((sum, weight) => sum + weight, 0);
  const exact = positive.map((weight) => totalUnits * weight / total);
  const units = exact.map(Math.floor);
  let remainder = totalUnits - units.reduce((sum, value) => sum + value, 0);
  const order = exact
    .map((value, index) => ({ index, fraction: value - Math.floor(value) }))
    .sort((left, right) => right.fraction - left.fraction || left.index - right.index);
  for (let index = 0; index < remainder; index += 1) {
    units[order[index % order.length].index] += 1;
  }
  for (const emptyIndex of units.flatMap((value, index) => value === 0 ? [index] : [])) {
    const donorIndex = units.reduce(
      (best, value, index) => value > units[best] ? index : best,
      0,
    );
    if (units[donorIndex] <= 1) break;
    units[donorIndex] -= 1;
    units[emptyIndex] = 1;
  }
  return units;
};
