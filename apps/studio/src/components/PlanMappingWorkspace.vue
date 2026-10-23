<script setup lang="ts">
import {
  ChevronDown,
  Plus,
  Trash2,
  Trees,
} from "@lucide/vue";
import { computed, ref, watch } from "vue";
import {
  RoleChoiceNav,
  RolePurposePanel,
  RoleDisabledState,
} from "@verdflow/role-guide-ui";
import type {
  AssetCatalogAssetInfo,
  AssetCatalogSummary,
  AssetCatalogCollectionInfo,
  StrategyRoleInfo,
} from "../api";
import StudioAssetBrowser from "./StudioAssetBrowser.vue";
import LibraryThumbnail from "../features/library/LibraryThumbnail.vue";
import {
  balanceCandidateWeights,
  defaultCandidate,
  hasRepeatedTarget,
} from "../features/shared/roleMapping";
import type {
  MappingCandidateDraft,
  RoleMappingDraft,
  MappingScaleMode,
} from "../features/shared/roleMapping";

const props = defineProps<{
  roles: StrategyRoleInfo[];
  assets: AssetCatalogAssetInfo[];
  catalogs: AssetCatalogSummary[];
  collections: AssetCatalogCollectionInfo[];
  mappings: RoleMappingDraft;
  enabled: Record<string, boolean>;
  disabled?: boolean;
}>();
const emit = defineEmits<{
  "update:mappings": [value: RoleMappingDraft];
  "update:enabled": [value: Record<string, boolean>];
}>();
const active = ref("");
const pane = ref<"assets" | "selected">("assets");
watch(
  () => props.roles,
  (roles) => {
    if (!roles.some((role) => role.role_key === active.value))
      active.value = roles[0]?.role_key ?? "";
  },
  { immediate: true },
);
const role = computed(() =>
  props.roles.find((item) => item.role_key === active.value),
);
const candidates = computed(() => props.mappings[active.value] ?? []);
const enabledCount = computed(
  () =>
    props.roles.filter((item) => props.enabled[item.role_key] !== false).length,
);
const activeEnabled = computed(() => props.enabled[active.value] !== false);
const counts = computed(() =>
  Object.fromEntries(
    props.roles.map((item) => [
      item.role_key,
      props.mappings[item.role_key]?.length ?? 0,
    ]),
  ),
);
const assetMap = computed(
  () => new Map([
    ...Object.values(props.mappings).flat().flatMap((candidate) => candidate.asset ? [[candidate.assetId, candidate.asset] as const] : []),
    ...props.assets.map((asset) => [asset.asset_id, asset] as const),
  ]),
);
const replace = (items: MappingCandidateDraft[]) =>
  emit("update:mappings", { ...props.mappings, [active.value]: items });
const toggleAsset = (asset: AssetCatalogAssetInfo) => {
  if (props.disabled || !activeEnabled.value) return;
  const existing = candidates.value.some(
    (item) => item.assetId === asset.asset_id,
  );
  const items = existing
    ? candidates.value.filter((item) => item.assetId !== asset.asset_id)
    : role.value?.assignment_policy === "single"
      ? [defaultCandidate(asset.asset_id, asset)]
      : [...candidates.value, defaultCandidate(asset.asset_id, asset)];
  replace(balanceCandidateWeights(items));
};
const update = (
  candidate: MappingCandidateDraft,
  patch: Partial<MappingCandidateDraft>,
) =>
  replace(
    candidates.value.map((item) =>
      item.assetId === candidate.assetId ? { ...item, ...patch } : item,
    ),
  );
const numberChange = (
  candidate: MappingCandidateDraft,
  field: "weight" | "fixedScale" | "minScale" | "maxScale",
  event: Event,
) => {
  const value = Number((event.target as HTMLInputElement).value) / 100;
  if (!Number.isFinite(value) || value <= 0) return;
  if (field === "weight")
    replace(
      balanceCandidateWeights(candidates.value, candidate.assetId, value),
    );
  else update(candidate, { [field]: value });
};
const remove = (candidate: MappingCandidateDraft) =>
  replace(
    balanceCandidateWeights(
      candidates.value.filter((item) => item.assetId !== candidate.assetId),
    ),
  );
const toggleActive = (event: Event) => {
  const checked = (event.target as HTMLInputElement).checked;
  if (!checked && enabledCount.value <= 1) return;
  emit("update:enabled", { ...props.enabled, [active.value]: checked });
};
</script>

<template>
  <div
    class="plan-mapping-workspace"
    :class="{ 'role-disabled': !activeEnabled }"
    :data-pane="pane"
  >
    <aside class="plan-role-sidebar">
      <RoleChoiceNav
        vertical
        :roles="roles"
        :active-role-key="active"
        :enabled="enabled"
        :counts="counts"
        :disabled="disabled"
        @select="active = $event"
        @update:enabled="emit('update:enabled', $event)"
      />
    </aside>
    <section class="plan-plant-browser">
      <header class="plan-active-role">
        <strong>{{ role?.display_name }}</strong
        ><label
          ><input
            type="checkbox"
            :checked="activeEnabled"
            :disabled="disabled || (activeEnabled && enabledCount === 1)"
            @change="toggleActive"
          />启用此用途</label
        >
      </header>
      <RoleDisabledState
        v-if="!activeEnabled"
        :name="role?.display_name ?? '此用途'"
        :candidate-count="candidates.length"
      />
      <template v-else>
        <details v-if="role?.guide" class="plan-role-guide">
          <summary>用途示意与推荐植被<ChevronDown :size="14" /></summary>
          <RolePurposePanel :role="role" :roles="roles" />
        </details>
        <div class="plan-mobile-panes">
          <button
            type="button"
            :class="{ active: pane === 'assets' }"
            @click="pane = 'assets'"
          >
            选择植被</button
          ><button
            type="button"
            :class="{ active: pane === 'selected' }"
            @click="pane = 'selected'"
          >
            已选植被 {{ candidates.length }}
          </button>
        </div>
        <div v-show="pane === 'assets'" class="plan-browser-content">
          <StudioAssetBrowser :catalogs="catalogs" :mappings="mappings" :selected-ids="candidates.map(c => c.assetId)" :disabled="disabled" @toggle="toggleAsset" />
        </div>
      </template>
    </section>
    <aside
      v-if="activeEnabled"
      class="plan-selected-panel"
      :class="{ 'mobile-visible': pane === 'selected' }"
    >
      <header>
        <strong>已选植被</strong
        ><span
          >{{ candidates.length }} 种 · 总权重
          {{ candidates.length ? 100 : 0 }}%</span
        >
      </header>
      <p v-if="hasRepeatedTarget(candidates)" class="mapping-duplicate-note" role="status">部分植被引用同一模型，权重将分别计入。</p>
      <div class="plan-selected-list">
        <article
          v-for="candidate in candidates"
          :key="candidate.assetId"
          class="plan-candidate"
        >
          <div class="plan-candidate-heading">
            <span class="plan-candidate-image"
              ><LibraryThumbnail
                :resource-id="
                  assetMap.get(candidate.assetId)?.thumbnail_resource_id ?? null
                "
                ><Trees :size="18" /></LibraryThumbnail></span
            ><strong>{{
              candidate.displayNameSnapshot ||
              assetMap.get(candidate.assetId)?.display_name ||
              candidate.assetId
            }}</strong
            ><button
              class="icon-button compact danger"
              type="button"
              title="移除植被"
              :disabled="disabled || !activeEnabled"
              @click="remove(candidate)"
            >
              <Trash2 :size="15" />
            </button>
          </div>
          <small class="plan-candidate-source">{{ assetMap.get(candidate.assetId)?.source_catalog_name ?? (candidate.bindingCandidate?.catalog_link as { catalog_id?: string } | undefined)?.catalog_id }}</small>
          <div class="plan-candidate-fields">
            <label
              ><span>权重 (%)</span
              ><input
                type="number"
                min="1"
                max="100"
                :value="Math.round(candidate.weight * 100)"
                :disabled="
                  disabled || !activeEnabled || candidates.length === 1
                "
                @change="numberChange(candidate, 'weight', $event)"
            /></label>
            <label
              ><span>缩放</span
              ><select
                :value="candidate.scaleMode"
                :disabled="disabled || !activeEnabled"
                @change="
                  update(candidate, {
                    scaleMode: ($event.target as HTMLSelectElement)
                      .value as MappingScaleMode,
                  })
                "
              >
                <option value="catalog">资产默认</option>
                <option value="fixed">固定值</option>
                <option value="uniform_random">随机范围</option>
              </select></label
            >
            <label v-if="candidate.scaleMode === 'fixed'"
              ><span>缩放 (%)</span
              ><input
                type="number"
                min="1"
                :value="Math.round(candidate.fixedScale * 100)"
                :disabled="disabled || !activeEnabled"
                @input="numberChange(candidate, 'fixedScale', $event)"
            /></label>
            <template v-if="candidate.scaleMode === 'uniform_random'"
              ><label
                ><span>最小 (%)</span
                ><input
                  type="number"
                  min="1"
                  :value="Math.round(candidate.minScale * 100)"
                  :disabled="disabled || !activeEnabled"
                  @input="numberChange(candidate, 'minScale', $event)" /></label
              ><label
                ><span>最大 (%)</span
                ><input
                  type="number"
                  min="1"
                  :value="Math.round(candidate.maxScale * 100)"
                  :disabled="disabled || !activeEnabled"
                  @input="numberChange(candidate, 'maxScale', $event)" /></label
            ></template>
          </div>
        </article>
        <p v-if="!candidates.length" class="planting-empty">
          <Plus :size="18" />尚未选择植被
        </p>
      </div>
    </aside>
  </div>
</template>
