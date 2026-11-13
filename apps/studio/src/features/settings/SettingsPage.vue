<script setup lang="ts">
import {
  AlertCircle,
  CheckCircle2,
  FileCog,
  FolderOpen,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Save,
} from "@lucide/vue";
import { onMounted } from "vue";

import type { ExternalRuntimeInfo } from "../../api";
import DismissibleNotice from "../../components/DismissibleNotice.vue";
import { useExternalRuntimes } from "./useExternalRuntimes";
import ApplicationUpdates from "./ApplicationUpdates.vue";
import GuideSettings from "../onboarding/GuideSettings.vue";
import { useGuidePage } from "../onboarding/context";
import { requiredRuntimeIds, runtimeReadiness } from "../onboarding/runtimeReadiness";

const settings = useExternalRuntimes();
const guide = useGuidePage("runtimes", () => ({
  ...runtimeReadiness(settings.runtimes.value, guide?.progress.value.workflow ?? "live"),
  loading: settings.loading.value, busy: settings.action.value !== null, error: settings.errorMessage.value,
  actions: settings.runtimes.value
    .filter(runtime => !runtime.available && requiredRuntimeIds(guide?.progress.value.workflow ?? "live").includes(runtime.runtime_id))
    .map(runtime => ({ label: `选择 ${runtime.display_name} 路径`, disabled: !settings.managementAvailable.value,
      run: () => settings.browse(runtime) })),
}));

onMounted(() => void settings.initialize());

const isBusy = (runtimeId: string) =>
  settings.action.value?.runtimeId === runtimeId;

const sourceLabel = (runtime: ExternalRuntimeInfo) => {
  if (runtime.resolution_source === "manual") return "手动配置";
  if (runtime.resolution_source === "auto") return "自动发现";
  return "未配置";
};

const detailLabel = (runtime: ExternalRuntimeInfo) => {
  if (runtime.available) return "可用";
  if (runtime.detail_code === "file_not_found") return "文件不存在";
  if (runtime.detail_code === "directory_not_found") return "目录不存在";
  if (runtime.detail_code === "missing_sdk_directory") return "缺少 SDK 目录";
  if (runtime.detail_code === "missing_sdk_ac_conf") return "缺少 ac_conf.js";
  if (runtime.detail_code === "missing_sdk_ac_min") return "缺少 ac.min.js";
  if (runtime.detail_code === "missing_executable") return "缺少 Explorer 执行文件";
  if (runtime.detail_code.startsWith("missing_")) {
    return `缺少 ${runtime.detail_code.slice("missing_".length)}`;
  }
  return "未找到";
};
</script>

<template>
  <section class="settings-page">
    <header class="page-header settings-header">
      <div>
        <span class="page-header__eyebrow">系统</span>
        <h1>设置</h1>
        <p>管理本机外部运行环境与默认路径。</p>
      </div>
      <button class="icon-button" type="button" title="刷新" @click="settings.refresh">
        <RefreshCw :size="17" :class="{ spinning: settings.loading.value }" />
      </button>
    </header>

    <DismissibleNotice :message="settings.errorMessage.value" class="inline-alert settings-alert" @dismiss="settings.errorMessage.value = null">
      <div>
        <strong>设置未保存</strong>
        <p>{{ settings.errorMessage.value }}</p>
      </div>
      <template #actions><button type="button" @click="settings.refresh">重新读取</button></template>
    </DismissibleNotice>
    <DismissibleNotice v-if="!settings.errorMessage.value" :message="settings.notice.value" class="model-services-notice" status @dismiss="settings.notice.value = null">
      <CheckCircle2 :size="16" />{{ settings.notice.value }}
    </DismissibleNotice>

    <div class="settings-summary" aria-label="外部运行环境概览">
      <span><small>外部运行环境</small><strong>{{ settings.runtimes.value.length }}</strong></span>
      <span><small>当前可用</small><strong>{{ settings.availableCount.value }}</strong></span>
    </div>

    <main class="settings-content">
      <GuideSettings />
      <ApplicationUpdates />
      <header class="settings-section-header" data-guide-anchor="runtimes">
        <div>
          <h2>外部运行环境</h2>
        </div>
      </header>

      <div v-if="settings.loading.value && !settings.runtimes.value.length" class="settings-loading">
        <LoaderCircle :size="19" class="spinning" />正在读取运行环境
      </div>

      <article
        v-for="runtime in settings.runtimes.value"
        v-else
        :key="runtime.runtime_id"
        class="runtime-setting-row"
      >
        <div class="runtime-setting-row__identity">
          <span class="runtime-setting-row__icon">
            <FileCog v-if="runtime.path_kind === 'file'" :size="20" />
            <FolderOpen v-else :size="20" />
          </span>
          <div>
            <h3>{{ runtime.display_name }}</h3>
            <span>{{ runtime.runtime_id }}</span>
          </div>
        </div>

        <div class="runtime-setting-row__status">
          <span :class="['runtime-state', { 'runtime-state--available': runtime.available }]">
            <CheckCircle2 v-if="runtime.available" :size="15" />
            <AlertCircle v-else :size="15" />
            {{ detailLabel(runtime) }}
          </span>
          <small>{{ sourceLabel(runtime) }}</small>
        </div>

        <label class="runtime-path-field">
          <span>{{ runtime.path_kind === "file" ? "可执行文件" : "Explorer 安装文件夹" }}</span>
          <input
            v-model="settings.drafts[runtime.runtime_id]"
            type="text"
            :placeholder="runtime.resolved_path || (runtime.runtime_id === 'dts-explorer' ? '选择 Explorer 7.1 安装文件夹' : '尚未配置')"
            :disabled="isBusy(runtime.runtime_id)"
          />
          <small v-if="runtime.resolved_path">当前：{{ runtime.resolved_path }}</small>
          <small v-if="runtime.runtime_id === 'dts-explorer'" class="runtime-path-field__hint">
            请选择同时包含 SDK 和 AirCityExplorer 的文件夹
          </small>
          <small v-else-if="!runtime.resolved_path && runtime.required_names.length">
            需要：{{ runtime.required_names.join("、") }}
          </small>
        </label>

        <div class="runtime-setting-row__actions">
          <button
            class="secondary-button"
            type="button"
            :disabled="
              isBusy(runtime.runtime_id) ||
              !settings.managementAvailable.value ||
              !settings.drafts[runtime.runtime_id]?.trim()
            "
            @click="settings.save(runtime)"
          >
            <Save :size="16" />保存
          </button>
          <button
            class="secondary-button"
            type="button"
            :disabled="
              isBusy(runtime.runtime_id) ||
              !settings.managementAvailable.value ||
              !settings.drafts[runtime.runtime_id]?.trim()
            "
            @click="settings.clear(runtime)"
          >
            <RotateCcw :size="16" />自动发现
          </button>
          <button
            class="primary-button"
            type="button"
            :disabled="
              isBusy(runtime.runtime_id) ||
              !settings.managementAvailable.value ||
              !settings.runtimes.value.length
            "
            @click="settings.browse(runtime)"
          >
            <LoaderCircle v-if="isBusy(runtime.runtime_id)" :size="16" class="spinning" />
            <FolderOpen v-else :size="16" />{{ runtime.path_kind === "file" ? "选择程序" : "选择文件夹" }}
          </button>
        </div>
      </article>
    </main>
  </section>
</template>
