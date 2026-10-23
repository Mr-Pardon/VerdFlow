<script setup lang="ts">
import { X, Check, ChevronDown, AlertCircle } from "@lucide/vue";
import { computed, ref, watch, onMounted, onBeforeUnmount } from "vue";
import {
  RoleChoiceNav,
  RolePurposePanel,
  RoleDisabledState,
} from "@verdflow/role-guide-ui";
import type { LiveConfiguration } from "../features/useLiveConfiguration";
import MappingAssetBrowser from "./MappingAssetBrowser.vue";
import MappingCandidateList from "./MappingCandidateList.vue";
import MappingSaveDialog from "./MappingSaveDialog.vue";
import { scrollPanelWheel } from "../shared/panelScroll";

const props = defineProps<{ controller: LiveConfiguration }>();
const {
  selectedStrategy,
  activeRoleKey,
  mappingOpen,
  mappingReady,
  missingRoles,
  activeRole,
  activeRoleEnabled,
  activeCandidates,
  roleEnabledStates,
  roleCandidateCounts,
  enabledRoleCount,
  mappingError,
  profileSaveOpen,
  savingProfile,
  toggleRole,
  applyMapping,
  cancelMapping,
} = props.controller;
const activePane = ref<"assets" | "candidates">("assets");
const guideExpanded = ref(window.innerHeight >= 760);
const keepGalleryVisible = () => {
  if (window.innerHeight < 760) guideExpanded.value = false;
};
watch(mappingOpen, (open) => {
  if (!open) return;
  guideExpanded.value = window.innerHeight >= 760;
  activePane.value = "assets";
});
onMounted(() => window.addEventListener("resize", keepGalleryVisible));
onBeforeUnmount(() => window.removeEventListener("resize", keepGalleryVisible));
const missingRoleNames = computed(() =>
  (selectedStrategy.value?.roles ?? [])
    .filter((role) => missingRoles.value.includes(role.role_key))
    .map((role) => role.display_name)
    .join("、"),
);
</script>

<template>
  <div v-if="mappingOpen" class="modal-backdrop" @wheel="scrollPanelWheel">
    <section
      class="mapping-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="mapping-title"
    >
      <header class="modal-header">
        <div class="mapping-title">
          <span
            >{{ selectedStrategy?.summary.display_name }} · v{{
              selectedStrategy?.summary.strategy_version
            }}</span
          >
          <h2 id="mapping-title">配置植被搭配</h2>
        </div>
        <button
          class="mapping-dialog-close"
          type="button"
          title="关闭植被搭配"
          :disabled="savingProfile"
          @click="cancelMapping"
        >
          <X :size="20" />
        </button>
      </header>
      <p
        v-if="mappingError && !profileSaveOpen"
        class="mapping-inline-error"
        role="alert"
      >
        <AlertCircle :size="16" />{{ mappingError }}
      </p>
      <div class="mapping-workspace">
        <aside class="role-list">
          <div class="role-list-heading">
            <strong>植被用途</strong
            ><span>{{ selectedStrategy?.roles.length }} 项</span>
          </div>
          <RoleChoiceNav
            vertical
            :roles="selectedStrategy?.roles ?? []"
            :active-role-key="activeRoleKey"
            :enabled="roleEnabledStates"
            :counts="roleCandidateCounts"
            :disabled="savingProfile"
            @update:enabled="controller.setRoleEnabled($event)"
            @select="activeRoleKey = $event"
          />
        </aside>
        <div class="mapping-main">
          <section class="role-context" :aria-label="activeRole?.display_name">
            <div class="active-role-heading">
              <div>
                <strong>{{ activeRole?.display_name }}</strong
                ><span>{{
                  activeRole?.assignment_policy === "single"
                    ? "单种植被"
                    : "多种植被搭配"
                }}</span>
              </div>
              <label class="role-enable-toggle"
                ><input
                  type="checkbox"
                  :checked="activeRoleEnabled"
                  :disabled="activeRoleEnabled && enabledRoleCount === 1"
                  @change="toggleRole"
                />启用此用途</label
              >
            </div>
            <details
              v-if="activeRoleEnabled && activeRole?.guide"
              class="mapping-role-guide"
              :open="guideExpanded"
              @toggle="
                guideExpanded = ($event.target as HTMLDetailsElement).open
              "
            >
              <summary>
                <span>用途示意与推荐植被</span><ChevronDown :size="15" />
              </summary>
              <RolePurposePanel
                :role="activeRole"
                :roles="selectedStrategy?.roles ?? []"
              />
            </details>
          </section>
          <RoleDisabledState
            v-if="!activeRoleEnabled"
            :name="activeRole?.display_name ?? '此用途'"
            :candidate-count="activeCandidates.length"
          />
          <template v-else>
            <div
              class="mapping-pane-tabs"
              role="tablist"
              aria-label="搭配配置视图"
            >
              <button
                id="mapping-assets-tab"
                type="button"
                role="tab"
                :aria-selected="activePane === 'assets'"
                aria-controls="mapping-assets-pane"
                @click="activePane = 'assets'"
              >
                选择植被
              </button>
              <button
                id="mapping-candidates-tab"
                type="button"
                role="tab"
                :aria-selected="activePane === 'candidates'"
                aria-controls="mapping-candidates-pane"
                @click="activePane = 'candidates'"
              >
                已选植被 {{ activeCandidates.length }}
              </button>
            </div>
            <div class="mapping-columns" :data-active-pane="activePane">
              <div id="mapping-assets-pane" class="mapping-assets-pane">
                <MappingAssetBrowser :controller="controller" />
              </div>
              <div id="mapping-candidates-pane" class="mapping-candidates-pane">
                <MappingCandidateList :controller="controller" />
              </div>
            </div>
          </template>
        </div>
      </div>
      <footer class="modal-footer">
        <div
          class="mapping-completion"
          :class="{ incomplete: missingRoles.length }"
        >
          <AlertCircle v-if="missingRoles.length" :size="17" /><Check
            v-else
            :size="17"
          />
          <span>{{
            missingRoles.length
              ? `尚需配置：${missingRoleNames}`
              : `已启用 ${enabledRoleCount} 项植被用途`
          }}</span>
        </div>
        <div class="mapping-apply-actions">
          <button
            type="button"
            class="mapping-cancel"
            :disabled="savingProfile"
            @click="cancelMapping"
          >
            取消
          </button>
          <button
            type="button"
            class="secondary-button"
            :disabled="!mappingReady || savingProfile"
            @click="applyMapping"
          >
            <Check :size="17" /><span>应用</span>
          </button>
        </div>
      </footer>
    </section>
  </div>
  <MappingSaveDialog v-if="profileSaveOpen" :controller="controller" />
</template>
