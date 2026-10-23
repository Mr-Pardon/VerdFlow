<script setup lang="ts">
import {
  BookOpen,
  Check,
  ChevronRight,
  Download,
  FileInput,
  LoaderCircle,
  PackageOpen,
  RefreshCw,
  Search,
  ShieldCheck,
  ShieldOff,
  Trash2,
} from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";

import type { StrategyLibrarySummary } from "../../api";
import { confirmAction } from "../../components/confirmDialog";
import { useStrategyLibrary } from "./useStrategyLibrary";
import { usePackageDropTarget } from "./usePackageDropTarget";
import { useGuidePage } from "../onboarding/context";

const library = useStrategyLibrary();
useGuidePage("strategies", () => ({
  ready: library.strategies.value.some(item => item.trust_state === "trusted"),
  status: `已导入 ${library.strategies.value.length} 个策略版本，已信任 ${library.strategies.value.filter(item => item.trust_state === "trusted").length} 个`,
  loading: library.loading.value, busy: library.action.value !== null, error: library.errorMessage.value,
  refresh: library.refresh,
  actions: [
    { label: "导入策略包", disabled: !library.managementAvailable.value || !library.canSelectSourceFiles, run: library.importStrategy },
    { label: "批量信任", disabled: !library.managementAvailable.value || !library.trustableStrategies.value.length, run: () => trustAll() },
  ],
  authorAction: { label: "VFS Author", disabled: !library.managementAvailable.value || !library.canExportTarget, run: library.exportAuthoringKit },
}));
const query = ref("");
const expandedStrategyIds = ref<string[]>([]);
const packageDrop = usePackageDropTarget({
  extension: "vfs",
  disabled: () =>
    !library.managementAvailable.value || library.action.value !== null,
  onDropPaths: ({ paths, rejectedNames, pathFailures }) =>
    library.importStrategyPaths(paths, rejectedNames, pathFailures),
});

interface StrategyGroup {
  strategyId: string;
  displayName: string;
  versions: StrategyLibrarySummary[];
}

const visibleGroups = computed(() => {
  const normalized = query.value.trim().toLocaleLowerCase();
  if (!normalized) return library.groups.value;
  return library.groups.value.filter(
    (group) =>
      group.displayName.toLocaleLowerCase().includes(normalized) ||
      group.strategyId.toLocaleLowerCase().includes(normalized) ||
      group.versions.some((version) =>
        version.strategy_version.toLocaleLowerCase().includes(normalized),
      ),
  );
});

const intentSummary = computed(() => textValue(library.detail.value?.intent.summary));
const intentKeywords = computed(() => textList(library.detail.value?.intent.design_keywords));
const intentAvoid = computed(() => textList(library.detail.value?.intent.avoid));
const terrainHint = computed(() =>
  textValue(library.detail.value?.terrain_suitability.selection_hint),
);
const terrainFeatures = computed(() => {
  const value = library.detail.value?.terrain_suitability.recommended_numeric_features;
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value).map(([key, limits]) => ({
    key,
    value: readableLimits(limits),
  }));
});

const selectedVersionForGroup = (group: StrategyGroup) =>
  group.versions.find((version) => version.package_hash === library.selectedHash.value) ?? null;

const isGroupExpanded = (strategyId: string) =>
  Boolean(query.value.trim()) || expandedStrategyIds.value.includes(strategyId);

const setGroupExpanded = (strategyId: string, expanded: boolean) => {
  expandedStrategyIds.value = expanded
    ? [...new Set([...expandedStrategyIds.value, strategyId])]
    : expandedStrategyIds.value.filter((item) => item !== strategyId);
};

const chooseGroup = async (group: StrategyGroup) => {
  if (group.versions.length === 1) {
    setGroupExpanded(group.strategyId, false);
    await library.selectStrategy(group.versions[0]?.package_hash ?? "");
    return;
  }
  setGroupExpanded(group.strategyId, !isGroupExpanded(group.strategyId));
};

const chooseVersion = async (group: StrategyGroup, packageHash: string) => {
  setGroupExpanded(group.strategyId, true);
  await library.selectStrategy(packageHash);
};

const changeTrust = async () => {
  const summary = library.detail.value?.summary;
  if (!summary) return;
  if (summary.trust_state === "trusted") {
    const confirmed = await confirmAction({
      title: "撤销策略信任？",
      message: "撤销后，该精确策略包将不能用于新的种植任务。",
      confirmLabel: "撤销信任",
      tone: "danger",
    });
    if (!confirmed) return;
    await library.setTrust(false);
    return;
  }
  const confirmed = await confirmAction({
    title: "信任策略包？",
    message: "信任仅代表你允许执行该精确包内容，不等同于安全沙盒。",
    confirmLabel: "确认信任",
  });
  if (!confirmed) return;
  await library.setTrust(true);
};
const trustAll = async () => {
  const targets = [...library.trustableStrategies.value];
  if (!targets.length) return;
  const confirmed = await confirmAction({ title: `信任 ${targets.length} 个策略包？`,
    message: '这些策略的生成器将获准在本机执行。信任不等同于安全沙盒，已撤销信任的包不在此次范围内。',
    detail: targets.map(item => `${item.display_name} · v${item.strategy_version}`).join('\n'), confirmLabel: '确认批量信任' });
  if (confirmed) await library.trustAll(targets);
};

const uninstall = async () => {
  const summary = library.detail.value?.summary;
  if (!summary) return;
  const confirmed = await confirmAction({
    title: "删除策略版本？",
    message: `将删除“${summary.display_name}”版本 ${summary.strategy_version}。历史项目快照不会被删除。`,
    confirmLabel: "删除版本",
    tone: "danger",
  });
  if (!confirmed) return;
  await library.uninstall();
};

onMounted(() => void library.initialize());

watch(
  () => library.selectedHash.value,
  (packageHash) => {
    if (!packageHash) return;
    const group = library.groups.value.find((item) =>
      item.versions.some((version) => version.package_hash === packageHash),
    );
    if (group && group.versions.length > 1) setGroupExpanded(group.strategyId, true);
  },
);

const textValue = (value: unknown) => (typeof value === "string" ? value : "");
const textList = (value: unknown) =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
const readableLimits = (value: unknown) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "-";
  return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${key}: ${String(item)}`)
    .join(" · ");
};
const trustLabel = (state: string | null) =>
  state === "trusted" ? "已信任" : state === "revoked" ? "已撤销" : "未信任";
</script>

<template>
  <section
    class="library-page package-drop-target"
    @dragenter="packageDrop.onDragEnter"
    @dragover="packageDrop.onDragOver"
    @dragleave="packageDrop.onDragLeave"
    @drop="packageDrop.onDrop"
  >
    <div v-if="packageDrop.active.value" class="package-drop-overlay" aria-hidden="true">
      <FileInput :size="25" />
      <strong>释放以导入策略包</strong>
      <span>支持一个或多个 .vfs 文件</span>
    </div>
    <header class="page-header library-page__header">
      <div>
        <span class="page-header__eyebrow">基础资源</span>
        <h1>策略库</h1>
        <p>管理可拔插种植策略及其精确信任状态。</p>
      </div>
      <div class="page-actions">
        <label class="search-control">
          <Search :size="17" />
          <span class="sr-only">搜索策略</span>
          <input v-model="query" type="search" placeholder="搜索策略名称、ID 或版本" />
        </label>
        <button class="icon-button" type="button" title="重新读取已安装策略" @click="library.refresh">
          <RefreshCw :size="17" :class="{ spinning: library.loading.value }" />
        </button>
        <button class="secondary-button" type="button"
          :disabled="!library.managementAvailable.value || !library.canExportTarget || library.action.value !== null"
          title="导出供 Agent 创作策略使用的模板、契约与自检说明" @click="library.exportAuthoringKit">
          <LoaderCircle v-if="library.action.value === 'kit'" :size="17" class="spinning" />
          <PackageOpen v-else :size="17" />
          VFS Author
        </button>
        <button
          class="secondary-button"
          type="button"
          :disabled="!library.managementAvailable.value || library.action.value !== null || !library.trustableStrategies.value.length"
          @click="trustAll"
        ><ShieldCheck :size="17" />批量信任<span v-if="library.trustableStrategies.value.length">{{ library.trustableStrategies.value.length }}</span></button>
        <button
          class="primary-button"
          type="button"
          :disabled="!library.managementAvailable.value || !library.canSelectSourceFiles || library.action.value !== null"
          title="可选择一个或多个 VerdFlow .vfs 策略包"
          data-guide-anchor="strategies"
          @click="library.importStrategy"
        >
          <LoaderCircle v-if="library.action.value === 'import'" :size="17" class="spinning" />
          <FileInput v-else :size="17" />
          导入策略包
        </button>
      </div>
    </header>

    <div v-if="library.errorMessage.value" class="inline-alert library-alert" role="alert">
      <div><strong>策略库操作未完成</strong><p>{{ library.errorMessage.value }}</p></div>
      <button type="button" @click="library.refresh">重新读取</button>
    </div>
    <div v-if="library.noticeMessage.value" class="inline-alert inline-alert--success library-alert" role="status">
      <div><strong>策略包操作完成</strong><p>{{ library.noticeMessage.value }}</p></div>
    </div>

    <div class="library-workspace">
      <aside class="library-master">
        <header>
          <span>本地策略</span>
          <strong>{{ library.groups.value.length }}</strong>
        </header>
        <div v-if="library.loading.value && !library.strategies.value.length" class="library-loading">
          <LoaderCircle :size="18" class="spinning" />正在读取
        </div>
        <div v-else-if="visibleGroups.length" class="strategy-master-list">
          <section
            v-for="group in visibleGroups"
            :key="group.strategyId"
            :class="{ selected: selectedVersionForGroup(group) }"
          >
            <button
              class="strategy-group-button"
              type="button"
              :aria-expanded="group.versions.length > 1 ? isGroupExpanded(group.strategyId) : undefined"
              @click="chooseGroup(group)"
            >
              <span>
                <strong>{{ group.displayName }}</strong>
                <small>{{ group.strategyId }}</small>
              </span>
              <span class="strategy-group-meta">
                <template v-if="selectedVersionForGroup(group)">当前 v{{ selectedVersionForGroup(group)?.strategy_version }}</template>
                <template v-else-if="group.versions.length === 1">
                  v{{ group.versions[0]?.strategy_version }} · {{ trustLabel(group.versions[0]?.trust_state ?? null) }}
                </template>
                <template v-else>{{ group.versions.length }} 个版本</template>
              </span>
              <ChevronRight
                v-if="group.versions.length > 1"
                :size="15"
                :class="{ expanded: isGroupExpanded(group.strategyId) }"
              />
              <Check v-else-if="selectedVersionForGroup(group)" :size="15" />
            </button>

            <div v-if="group.versions.length > 1 && isGroupExpanded(group.strategyId)" class="strategy-version-list">
              <button
                v-for="version in group.versions"
                :key="version.package_hash"
                class="strategy-version-button"
                type="button"
                :class="{ active: library.selectedHash.value === version.package_hash }"
                @click="chooseVersion(group, version.package_hash)"
              >
                <span class="status-dot" :data-tone="version.executable ? 'success' : version.trust_state === 'revoked' ? 'danger' : 'warning'" />
                <span><strong>v{{ version.strategy_version }}</strong><small>{{ trustLabel(version.trust_state) }}</small></span>
                <Check v-if="library.selectedHash.value === version.package_hash" :size="15" />
              </button>
            </div>
          </section>
        </div>
        <div v-else class="library-empty"><BookOpen :size="26" /><strong>没有匹配的策略</strong></div>
      </aside>

      <main class="library-detail">
        <div v-if="library.loadingDetail.value" class="library-loading"><LoaderCircle :size="20" class="spinning" />正在读取策略详情</div>
        <template v-else-if="library.detail.value">
          <header class="library-detail__hero">
            <div>
              <span>种植策略</span>
              <h2>{{ library.detail.value.summary.display_name }}</h2>
              <p>{{ library.detail.value.description || '该策略没有补充描述。' }}</p>
            </div>
            <div class="library-detail__actions">
              <span class="library-status" :data-state="library.detail.value.summary.trust_state || 'unknown'">
                {{ trustLabel(library.detail.value.summary.trust_state) }}
              </span>
              <button class="secondary-button" type="button" :disabled="!library.managementAvailable.value || !library.canExportTarget || library.action.value !== null" @click="library.exportStrategy">
                <LoaderCircle v-if="library.action.value === 'export'" :size="16" class="spinning" />
                <Download v-else :size="16" />
                导出 .vfs
              </button>
              <button class="secondary-button" type="button" :disabled="!library.managementAvailable.value || library.action.value !== null" @click="changeTrust">
                <ShieldOff v-if="library.detail.value.summary.trust_state === 'trusted'" :size="16" />
                <ShieldCheck v-else :size="16" />
                {{ library.detail.value.summary.trust_state === 'trusted' ? '撤销信任' : '信任策略' }}
              </button>
              <button class="icon-button danger" type="button" title="删除这个精确版本" :disabled="!library.managementAvailable.value || library.action.value !== null" @click="uninstall">
                <Trash2 :size="17" />
              </button>
            </div>
          </header>

          <div class="library-detail__meta">
            <span><small>策略 ID</small><strong>{{ library.detail.value.summary.strategy_id }}</strong></span>
            <span><small>版本</small><strong>{{ library.detail.value.summary.strategy_version }}</strong></span>
            <span><small>Package Hash</small><strong>{{ library.detail.value.summary.package_hash.slice(0, 16) }}</strong></span>
            <span><small>执行状态</small><strong>{{ library.detail.value.summary.executable ? '可执行' : '不可执行' }}</strong></span>
          </div>

          <div class="strategy-detail-grid">
            <section>
              <header><h3>设计意图</h3><span>Intent</span></header>
              <p>{{ intentSummary || library.detail.value.description }}</p>
              <div v-if="intentKeywords.length" class="library-tags"><span v-for="item in intentKeywords" :key="item">{{ item }}</span></div>
              <div v-if="intentAvoid.length" class="strategy-avoid"><strong>避免</strong><span v-for="item in intentAvoid" :key="item">{{ item }}</span></div>
            </section>
            <section>
              <header><h3>地形适配</h3><span>Weak constraint</span></header>
              <p>{{ terrainHint || '该策略没有提供额外的地形选择建议。' }}</p>
              <dl v-if="terrainFeatures.length" class="terrain-feature-list">
                <div v-for="feature in terrainFeatures" :key="feature.key"><dt>{{ feature.key }}</dt><dd>{{ feature.value }}</dd></div>
              </dl>
            </section>
          </div>

          <section class="strategy-roles-section">
            <header><div><h3>植被用途</h3><p>按空间分工搭配植被，同一用途不限定具体物种。</p></div><strong>{{ library.detail.value.roles.length }}</strong></header>
            <div class="strategy-role-table">
              <div v-for="role in library.detail.value.roles" :key="role.role_key">
                <span class="role-required" :data-required="role.required">{{ role.required ? '必需' : '可选' }}</span>
                <span><strong>{{ role.display_name }}</strong><small>{{ role.role_key }}</small></span>
                <p>{{ role.description }}</p>
                <code>{{ role.assignment_policy }}</code>
              </div>
            </div>
          </section>
        </template>
        <div v-else class="library-empty library-empty--detail"><BookOpen :size="30" /><strong>选择一个策略查看详情</strong></div>
      </main>
    </div>
  </section>
</template>
