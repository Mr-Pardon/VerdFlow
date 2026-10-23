<script setup lang="ts">
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  SlidersHorizontal,
  Sprout,
  Trash2,
  Trees,
  X,
} from "@lucide/vue";
import { computed, ref } from "vue";
import { RoleChoiceNav, RolePurposePanel, RoleDisabledState } from "@verdflow/role-guide-ui";

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
  validCandidates,
} from "../features/shared/roleMapping";
import type {
  MappingCandidateDraft,
  MappingScaleMode,
  RoleMappingDraft,
} from "../features/shared/roleMapping";

const props = withDefaults(
  defineProps<{
    roles: StrategyRoleInfo[];
    assets: AssetCatalogAssetInfo[];
    catalogs?: AssetCatalogSummary[];
    collections?: AssetCatalogCollectionInfo[];
    mappings: RoleMappingDraft;
    enabled?: Record<string, boolean>;
    emptyMessage?: string;
    guided?: boolean;
    disabled?: boolean;
    activeRoleKey?: string;
  }>(),
  {
    emptyMessage: "选择策略与目标资产目录后，在这里完成植被角色映射。",
    collections: () => [],
    catalogs: () => [],
    enabled: () => ({}),
  },
);

const emit = defineEmits<{
  "update:mappings": [value: RoleMappingDraft];
  "update:enabled": [value: Record<string, boolean>];
  "update:activeRoleKey": [value: string];
}>();

const pickerRole = ref<StrategyRoleInfo | null>(null);
const pickerAssets = ref<AssetCatalogAssetInfo[]>([]);
const pickerSelection = ref<string[]>([]);
const pickerMappings = computed(() => ({
  ...props.mappings,
  __picker: pickerAssets.value.filter(asset => pickerSelection.value.includes(asset.asset_id))
    .map(asset => defaultCandidate(asset.asset_id, asset)),
}));
const mappedRoles = computed(() =>
  props.roles.map((role) => ({
    role,
    candidates: props.mappings[role.role_key] ?? [],
    enabled: props.enabled[role.role_key] !== false,
  })),
);
const assetById = computed(
  () => new Map([
    ...Object.values(props.mappings).flat().flatMap((candidate) => candidate.asset ? [[candidate.assetId, candidate.asset] as const] : []),
    ...pickerAssets.value.map((item) => [item.asset_id, item] as const),
    ...props.assets.map((item) => [item.asset_id, item] as const),
  ]),
);
const enabledCount = computed(
  () => mappedRoles.value.filter((item) => item.enabled).length,
);
const candidateCounts = computed(() => Object.fromEntries(
  mappedRoles.value.map((item) => [item.role.role_key, validCandidates(item.candidates) ? item.candidates.length : 0]),
));
const activeRoleIndex = computed(() =>
  Math.max(
    0,
    mappedRoles.value.findIndex(
      (item) => item.role.role_key === props.activeRoleKey,
    ),
  ),
);
const visibleRoles = computed(() =>
  props.guided
    ? mappedRoles.value.slice(activeRoleIndex.value, activeRoleIndex.value + 1)
    : mappedRoles.value,
);
const toggleRole = (key: string, event: Event) =>
  emit("update:enabled", {
    ...props.enabled,
    [key]: (event.target as HTMLInputElement).checked,
  });
const assetSelectionAvailable = computed(() => props.catalogs.length > 0);

const openAssetPicker = (role: StrategyRoleInfo) => {
  if (!assetSelectionAvailable.value || props.disabled) return;
  pickerRole.value = role;
  pickerAssets.value = [];
  pickerSelection.value = (props.mappings[role.role_key] ?? []).map(
    (candidate) => candidate.assetId,
  );
};

const closeAssetPicker = () => {
  pickerRole.value = null;
  pickerAssets.value = [];
  pickerSelection.value = [];
};

const togglePickerAsset = (asset: AssetCatalogAssetInfo) => {
  const assetId = asset.asset_id;
  pickerAssets.value = [...pickerAssets.value.filter(item => item.asset_id !== assetId), asset];
  pickerSelection.value = pickerSelection.value.includes(assetId)
    ? pickerSelection.value.filter((item) => item !== assetId)
    : [...pickerSelection.value, assetId];
};

const applyAssetPicker = () => {
  if (!pickerRole.value) return;
  const roleKey = pickerRole.value.role_key;
  const existing = new Map(
    (props.mappings[roleKey] ?? []).map((candidate) => [
      candidate.assetId,
      candidate,
    ]),
  );
  const existingCandidates = props.mappings[roleKey] ?? [];
  const defaultWeight = existingCandidates.length
    ? existingCandidates.reduce((sum, candidate) => sum + candidate.weight, 0) /
      existingCandidates.length
    : 1;
  emit("update:mappings", {
    ...props.mappings,
    [roleKey]: balanceCandidateWeights(
      pickerSelection.value.map((assetId) => {
        const candidate =
          existing.get(assetId) ??
          defaultCandidate(assetId, assetById.value.get(assetId));
        return existing.has(assetId)
          ? candidate
          : { ...candidate, weight: defaultWeight };
      }),
    ),
  });
  closeAssetPicker();
};

const updateCandidate = (
  roleKey: string,
  assetId: string,
  patch: Partial<MappingCandidateDraft>,
) => {
  emit("update:mappings", {
    ...props.mappings,
    [roleKey]: (props.mappings[roleKey] ?? []).map((candidate) =>
      candidate.assetId === assetId ? { ...candidate, ...patch } : candidate,
    ),
  });
};

const removeCandidate = (roleKey: string, assetId: string) => {
  emit("update:mappings", {
    ...props.mappings,
    [roleKey]: balanceCandidateWeights(
      (props.mappings[roleKey] ?? []).filter(
        (candidate) => candidate.assetId !== assetId,
      ),
    ),
  });
};

const updateNumber = (
  roleKey: string,
  candidate: MappingCandidateDraft,
  field: "weight" | "fixedScale" | "minScale" | "maxScale",
  event: Event,
) => {
  const value = Number((event.target as HTMLInputElement).value);
  if (field === "weight") {
    if (!Number.isFinite(value) || value <= 0) return;
    emit("update:mappings", {
      ...props.mappings,
      [roleKey]: balanceCandidateWeights(
        props.mappings[roleKey] ?? [],
        candidate.assetId,
        value / 100,
      ),
    });
    return;
  }
  if (!Number.isFinite(value) || value <= 0) return;
  updateCandidate(roleKey, candidate.assetId, {
    [field]: value / 100,
  });
};

const scalePercent = (value: number) => Math.round(value * 100);

const updateScaleMode = (
  roleKey: string,
  candidate: MappingCandidateDraft,
  event: Event,
) => {
  updateCandidate(roleKey, candidate.assetId, {
    scaleMode: (event.target as HTMLSelectElement).value as MappingScaleMode,
  });
};

const roleStatus = (role: StrategyRoleInfo, count: number) =>
  count > 0
    ? props.guided
      ? "已配置"
      : "已映射"
    : role.required
      ? props.guided
        ? "待选择植被"
        : "需要映射"
      : "可选";
const scaleLabel = (candidate: MappingCandidateDraft) =>
  candidate.scaleMode === "fixed"
    ? `缩放 ${scalePercent(candidate.fixedScale)}%`
    : candidate.scaleMode === "uniform_random"
      ? `缩放 ${scalePercent(candidate.minScale)}–${scalePercent(candidate.maxScale)}%`
      : "资产默认缩放";
</script>

<template>
  <div
    v-if="mappedRoles.length"
    class="role-mapping-list role-mapping-editor"
    :class="{ 'role-mapping-guided': guided }"
  >
    <RoleChoiceNav v-if="guided" :roles="roles" :active-role-key="mappedRoles[activeRoleIndex]?.role.role_key ?? ''"
      :enabled="enabled" :counts="candidateCounts" :disabled="disabled"
      @update:enabled="emit('update:enabled', $event)"
      @select="emit('update:activeRoleKey', $event)" />
    <article v-for="item in visibleRoles" :key="item.role.role_key">
      <header class="role-mapping-title">
        <span :data-ready="item.candidates.length > 0">
          {{
            item.enabled
              ? roleStatus(item.role, item.candidates.length)
              : "已关闭"
          }}
        </span>
        <div>
          <span v-if="!guided && item.role.guide" class="role-purpose-group">{{ item.role.guide.group_name }}</span>
          <strong>{{ item.role.display_name }}</strong>
          <small>{{
            item.role.description ||
            (guided
              ? "空间构图中的一类植被，可选择一种或多种植物。"
              : item.role.role_key)
          }}</small>
        </div>
        <label class="role-enable-toggle">
          <input
            type="checkbox"
            :aria-label="`启用${item.role.display_name}`"
            :checked="item.enabled"
            :disabled="disabled || (item.enabled && enabledCount === 1)"
            @change="toggleRole(item.role.role_key, $event)"
          />
          <span>启用</span>
        </label>
        <button
          class="icon-button compact"
          type="button"
          :disabled="disabled || !assetSelectionAvailable || !item.enabled"
          :title="
            assetSelectionAvailable
              ? `为${item.role.display_name}选择植被`
              : '选择资产库后可添加或替换植被'
          "
          @click="openAssetPicker(item.role)"
        >
          <Plus :size="15" />
        </button>
      </header>

      <RolePurposePanel v-if="guided && item.enabled" :role="item.role" :roles="roles" />
      <details v-else-if="item.role.guide && item.enabled" class="role-purpose-details">
        <summary>位置与搭配建议</summary>
        <RolePurposePanel :role="item.role" :roles="roles" />
      </details>

      <RoleDisabledState v-if="!item.enabled" :name="item.role.display_name" :candidate-count="item.candidates.length" />
      <div v-else-if="item.candidates.length" class="role-candidate-list">
        <p v-if="hasRepeatedTarget(item.candidates)" class="mapping-duplicate-note" role="status">部分植被引用同一模型，权重将分别计入。</p>
        <div
          v-for="candidate in item.candidates"
          :key="candidate.assetId"
          class="role-candidate"
        >
          <div class="role-candidate-identity">
            <LibraryThumbnail
              :resource-id="
                assetById.get(candidate.assetId)?.thumbnail_resource_id ?? null
              "
            >
              <span><Trees :size="15" /></span>
            </LibraryThumbnail>
            <div>
              <strong>{{
                candidate.displayNameSnapshot ||
                assetById.get(candidate.assetId)?.display_name
              }}</strong>
              <small>{{
                guided
                  ? `${Math.round(candidate.weight * 100)}% · ${scaleLabel(candidate)}`
                  : (candidate.bindingCandidate?.catalog_link as { asset_id?: string } | undefined)?.asset_id ?? candidate.assetId
              }}</small>
              <small>{{ assetById.get(candidate.assetId)?.source_catalog_name ?? (candidate.bindingCandidate?.catalog_link as { catalog_id?: string } | undefined)?.catalog_id }}</small>
            </div>
            <button
              class="icon-button compact danger"
              type="button"
              title="移除植被"
              :disabled="disabled"
              @click="removeCandidate(item.role.role_key, candidate.assetId)"
            >
              <Trash2 :size="14" />
            </button>
          </div>
          <component :is="guided ? 'details' : 'div'" class="role-candidate-options">
            <summary v-if="guided">
              <SlidersHorizontal :size="14" />分配与缩放
            </summary>
            <div class="role-candidate-settings">
              <label>
                <span>权重 (%)</span>
                <input
                  type="number"
                  min="1"
                  :max="100 - item.candidates.length + 1"
                  step="1"
                  :value="Math.round(candidate.weight * 100)"
                  :disabled="disabled || item.candidates.length === 1"
                  @change="
                    updateNumber(
                      item.role.role_key,
                      candidate,
                      'weight',
                      $event,
                    )
                  "
                />
              </label>
              <label class="scale-mode-field">
                <span>缩放</span>
                <select
                  :value="candidate.scaleMode"
                  :disabled="disabled"
                  @change="
                    updateScaleMode(item.role.role_key, candidate, $event)
                  "
                >
                  <option value="catalog">资产默认</option>
                  <option value="uniform_random">随机范围</option>
                  <option value="fixed">固定值</option>
                </select>
              </label>
              <label v-if="candidate.scaleMode === 'fixed'">
                <span>值 (%)</span>
                <input
                  type="number"
                  min="1"
                  step="1"
                  :value="scalePercent(candidate.fixedScale)"
                  :disabled="disabled"
                  @input="
                    updateNumber(
                      item.role.role_key,
                      candidate,
                      'fixedScale',
                      $event,
                    )
                  "
                />
              </label>
              <template v-else-if="candidate.scaleMode === 'uniform_random'">
                <label
                  ><span>最小 (%)</span
                  ><input
                    type="number"
                    min="1"
                    step="1"
                    :value="scalePercent(candidate.minScale)"
                    :disabled="disabled"
                    @input="
                      updateNumber(
                        item.role.role_key,
                        candidate,
                        'minScale',
                        $event,
                      )
                    "
                /></label>
                <label
                  ><span>最大 (%)</span
                  ><input
                    type="number"
                    min="1"
                    step="1"
                    :value="scalePercent(candidate.maxScale)"
                    :disabled="disabled"
                    @input="
                      updateNumber(
                        item.role.role_key,
                        candidate,
                        'maxScale',
                        $event,
                      )
                    "
                /></label>
              </template>
            </div>
          </component>
        </div>
      </div>
      <button
        v-else
        class="role-add-button"
        type="button"
        :disabled="disabled || !assetSelectionAvailable"
        :title="assetSelectionAvailable ? '选择植被' : '选择资产库后可添加植被'"
        @click="openAssetPicker(item.role)"
      >
        <Plus :size="14" />选择植被
      </button>
    </article>
    <footer
      v-if="guided && mappedRoles.length > 1"
      class="role-guide-next"
    >
      <span>{{ activeRoleIndex + 1 }} / {{ mappedRoles.length }} 项植被用途</span>
      <div class="role-guide-actions">
        <button
          v-if="activeRoleIndex > 0"
          class="secondary-button"
          type="button"
          :disabled="disabled"
          @click="emit('update:activeRoleKey', mappedRoles[activeRoleIndex - 1].role.role_key)"
        >
          <ChevronLeft :size="15" />
          <span>上一个：{{ mappedRoles[activeRoleIndex - 1].role.display_name }}</span>
        </button>
        <button
          v-if="activeRoleIndex < mappedRoles.length - 1"
          class="secondary-button"
          type="button"
          :disabled="disabled"
          @click="emit('update:activeRoleKey', mappedRoles[activeRoleIndex + 1].role.role_key)"
        >
          <span>下一个：{{ mappedRoles[activeRoleIndex + 1].role.display_name }}</span>
          <ChevronRight :size="15" />
        </button>
      </div>
    </footer>
  </div>
  <div v-else class="planting-mapping-placeholder">
    <Sprout :size="24" />
    <p>{{ emptyMessage }}</p>
  </div>

  <Teleport to="body">
    <div
      v-if="pickerRole"
      class="studio-modal-backdrop"
      @mousedown.self="closeAssetPicker"
    >
      <section
        class="studio-modal asset-picker-modal"
        role="dialog"
        aria-modal="true"
      >
        <header>
          <div>
            <span>植被资产</span>
            <h3>为“{{ pickerRole.display_name }}”选择植被</h3>
          </div>
          <button
            class="icon-button"
            type="button"
            title="关闭"
            @click="closeAssetPicker"
          >
            <X :size="17" />
          </button>
        </header>
        <StudioAssetBrowser class="role-asset-browser" :catalogs="catalogs" :mappings="pickerMappings" :selected-ids="pickerSelection" :disabled="disabled" @toggle="togglePickerAsset" />
        <footer>
          <span>已选择 {{ pickerSelection.length }} 项</span>
          <button
            class="secondary-button"
            type="button"
            @click="closeAssetPicker"
          >
            取消
          </button>
          <button
            class="primary-button"
            type="button"
            @click="applyAssetPicker"
          >
            应用选择
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.asset-picker-modal { display:flex; flex-direction:column; height:min(760px, 90vh); }
.role-asset-browser { flex:1; min-height:0; }

.role-mapping-guided {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.role-purpose-group { display: block; margin-bottom: 5px; color: var(--color-text-muted); font-size: 11px; }
.role-purpose-details > summary { margin-top: 12px; font-size: 11px; color: var(--color-text-muted); cursor: pointer; }
.role-mapping-guided > article {
  padding: 18px 0 0;
  border: 0;
  border-top: 1px solid var(--color-border);
  border-radius: 0;
}
.role-mapping-guided .role-mapping-title {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
}
.role-mapping-guided .role-mapping-title > span:first-child {
  display: none;
}
.role-mapping-guided .role-mapping-title small {
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.6;
}
.role-mapping-guided .role-mapping-title strong {
  font-size: 15px;
  white-space: normal;
  overflow-wrap: anywhere;
}
.role-mapping-guided .role-candidate-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 270px), 1fr));
  gap: 10px;
}
.role-mapping-guided .role-candidate {
  margin: 0;
}
.role-mapping-guided .role-candidate-identity {
  grid-template-columns: 48px minmax(0, 1fr) 28px;
}
.role-mapping-guided .role-candidate-identity > img,
.role-mapping-guided .role-candidate-identity > span {
  width: 48px;
  height: 48px;
}
.role-mapping-guided .role-candidate-settings {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.role-candidate-options:not([open]) {
  display: block;
}
.role-candidate-options summary {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 0 0;
  cursor: pointer;
  color: var(--color-text-muted);
  font-size: 12px;
  list-style: none;
}
.role-candidate-options summary::-webkit-details-marker {
  display: none;
}
.role-candidate-options[open] summary {
  color: var(--color-accent);
}
.role-guide-next {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}
.role-guide-next > span {
  color: var(--color-text-muted);
  font-size: 12px;
}
.role-guide-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
  max-width: 100%;
  margin-left: auto;
}
.role-guide-next button {
  min-width: 0;
  max-width: 100%;
  white-space: normal;
  height: auto;
  min-height: 36px;
}
.role-guide-next button span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.role-guide-next button svg {
  flex-shrink: 0;
}
</style>
