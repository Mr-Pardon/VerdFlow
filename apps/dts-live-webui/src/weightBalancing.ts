import type { MappingCandidate } from "./types";

const WEIGHT_TOTAL = 100;

export const balanceCandidateWeights = (
  candidates: MappingCandidate[],
  preferredAssetId?: string,
  preferredWeight?: number,
): MappingCandidate[] => {
  if (!candidates.length) return [];
  if (candidates.length === 1) return [{ ...candidates[0], weight: 1 }];

  const preferredIndex = preferredAssetId
    ? candidates.findIndex((candidate) => candidate.asset_id === preferredAssetId)
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

export const balanceAssignments = <T extends { enabled?: boolean; candidates: MappingCandidate[] }>(
  assignments: Record<string, T>,
) => Object.fromEntries(
  Object.entries(assignments).map(([roleKey, assignment]) => [
    roleKey,
    { ...assignment, candidates: balanceCandidateWeights(assignment.candidates) },
  ]),
) as Record<string, T>;

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
