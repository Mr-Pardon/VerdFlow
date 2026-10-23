import { computed, onScopeDispose, shallowRef, watch } from "vue";
import type {
  AssignmentPreviewInput,
  AssignmentPreviewResult,
  StrategyPreviewResult,
  StrategyPreviewClient,
} from "./types";
import type { ExampleAppearanceRole } from "./types";

export function useExampleReferenceRanges(
  geometry: () => StrategyPreviewResult | null,
  appearance: () => ExampleAppearanceRole[],
  seed: () => number,
  client: StrategyPreviewClient,
) {
  const resolved = shallowRef<{
    signature: string;
    result: AssignmentPreviewResult;
  } | null>(null);
  const request = computed<AssignmentPreviewInput | null>(() => {
    const result = geometry();
    const roles = appearance();
    if (
      !result?.point_ids ||
      result.point_ids.length !== result.points.length ||
      !roles.some((role) =>
        role.candidates.some(
          (candidate) => candidate.reference_radius_m !== null,
        ),
      )
    )
      return null;
    return {
      random_seed: seed(),
      points: result.points.map((point, i) => [
        result.point_ids![i],
        result.role_keys[point[2]],
      ]),
      roles: roles.map((role) => ({
        role_key: role.role_key,
        candidates: role.candidates.map(({ asset_id, weight, scale }) => ({
          asset_id,
          weight,
          scale,
        })),
      })),
    };
  });
  const signature = computed(() => JSON.stringify(request.value));
  let revision = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const stop = watch(
    signature,
    () => {
      const current = ++revision;
      if (timer !== null) clearTimeout(timer);
      const input = request.value;
      if (!input) {
        resolved.value = null;
        return;
      }
      const key = signature.value;
      timer = setTimeout(async () => {
        try {
          const result = await client.previewAssetAssignments(input);
          if (
            current === revision &&
            result.choices.length === input.points.length
          )
            resolved.value = { signature: key, result };
        } catch {
          if (current === revision) resolved.value = null;
        }
      }, 100);
    },
    { immediate: true },
  );
  const radii = computed<Array<number | null>>(() => {
    if (!resolved.value || resolved.value.signature !== signature.value)
      return [];
    const byRole = new Map(
      appearance().map((role) => [role.role_key, role.candidates]),
    );
    const result = geometry();
    return resolved.value.result.choices.map(([index, scale], i) => {
      const role = result?.role_keys[result.points[i][2]];
      const reference = role
        ? byRole.get(role)?.[index]?.reference_radius_m
        : null;
      const radius = reference == null ? null : reference * scale;
      return radius !== null && Number.isFinite(radius) && radius > 0
        ? radius
        : null;
    });
  });
  onScopeDispose(() => {
    revision++;
    stop();
    if (timer !== null) clearTimeout(timer);
  });
  return radii;
}
