import type { AssetCatalogAssetInfo, AssetScalePolicy } from "../../api";
import type { ExampleAppearanceRole } from "@verdflow/strategy-preview-ui";
export type { ExampleAppearanceRole } from "@verdflow/strategy-preview-ui";
import type { AssignmentMap, RoleMappingDraft } from "./roleMapping";

export function strategyExampleAppearance(
  mappings: RoleMappingDraft,
  assignments: AssignmentMap,
  assets: AssetCatalogAssetInfo[],
): ExampleAppearanceRole[] {
  const catalog = new Map(assets.map((asset) => [asset.asset_id, asset]));
  return Object.entries(mappings).map(([role_key, drafts]) => {
    const saved = assignments[role_key]?.candidates;
    const snapshots = new Map(
      (Array.isArray(saved) ? saved : []).flatMap((raw) => {
        if (!raw || typeof raw !== "object") return [];
        const value = raw as Record<string, unknown>;
        return typeof value.asset_id === "string"
          ? [[value.asset_id, value] as const]
          : [];
      }),
    );
    return {
      role_key,
      candidates: drafts.flatMap((draft) => {
        const snapshot = draft.bindingCandidate ?? snapshots.get(draft.assetId);
        const asset = draft.asset ?? catalog.get(draft.assetId);
        const radius = snapshot
          ? snapshot.reference_radius_m
          : asset?.reference_radius_m;
        const scale =
          draft.scaleMode === "fixed"
            ? ({ mode: "fixed", value: draft.fixedScale } as const)
            : draft.scaleMode === "uniform_random"
              ? ({
                  mode: "uniform_random",
                  min: draft.minScale,
                  max: draft.maxScale,
                } as const)
              : scalePolicy(snapshot ? snapshot.scale : asset?.suggested_scale);
        if (!Number.isFinite(draft.weight) || draft.weight <= 0 || !scale)
          return [];
        return [
          {
            asset_id: draft.assetId,
            weight: draft.weight,
            scale,
            reference_radius_m:
              typeof radius === "number" &&
              Number.isFinite(radius) &&
              radius > 0
                ? radius
                : null,
          },
        ];
      }),
    };
  });
}

function scalePolicy(raw: unknown): AssetScalePolicy | null {
  if (raw == null) return { mode: "fixed", value: 1 };
  if (typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  if (value.mode === "fixed" && positive(value.value))
    return { mode: "fixed", value: value.value };
  if (
    value.mode === "uniform_random" &&
    positive(value.min) &&
    positive(value.max) &&
    value.min <= value.max
  )
    return { mode: "uniform_random", min: value.min, max: value.max };
  return null;
}
const positive = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value > 0;
