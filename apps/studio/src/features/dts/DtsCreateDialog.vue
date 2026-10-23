<script setup lang="ts">
import { FolderSearch, LoaderCircle, X } from "@lucide/vue";
import type { ModelProviderInfo } from "../../api";

defineProps<{
  sourcePath: string;
  projectName: string;
  projectPath: string;
  selectionMode: "ai_assisted" | "manual";
  confirmationMode: "automatic" | "manual";
  providerId: string;
  providers: ModelProviderInfo[];
  managementAvailable: boolean;
  busy: boolean;
  canSubmit: boolean;
  stage: "idle" | "creating_project" | "importing_source" | "starting_inspection";
  errorMessage: string | null;
}>();

const emit = defineEmits<{
  close: [];
  chooseLocation: [];
  submit: [];
  updateProjectName: [value: string];
  updateSelectionMode: [value: "ai_assisted" | "manual"];
  updateConfirmationMode: [value: "automatic" | "manual"];
  updateProviderId: [value: string];
}>();

const stageLabel = {
  idle: "创建并开始分析",
  creating_project: "正在创建项目",
  importing_source: "正在导入 FBX",
  starting_inspection: "正在提交模型检查",
};
</script>

<template>
  <div class="dialog-backdrop" @mousedown.self="$emit('close')">
    <section class="creation-dialog" role="dialog" aria-modal="true" aria-labelledby="create-title">
      <header>
        <div>
          <span>DTS · FBX 模型种植</span>
          <h2 id="create-title">创建种植项目</h2>
        </div>
        <button type="button" title="关闭" :disabled="busy" @click="$emit('close')">
          <X :size="19" />
        </button>
      </header>

      <div class="creation-dialog__body">
        <div class="source-summary">
          <span>Source</span>
          <strong :title="sourcePath">{{ sourcePath }}</strong>
        </div>

        <label class="form-field">
          <span>项目名称 <em>必填</em></span>
          <input
            :value="projectName"
            type="text"
            maxlength="120"
            :disabled="busy"
            @input="$emit('updateProjectName', ($event.target as HTMLInputElement).value)"
          />
        </label>

        <div class="form-field">
          <span>保存位置 <em>必填</em></span>
          <div class="path-picker">
            <div :title="projectPath">
              {{ projectPath || "选择父目录后自动创建项目目录" }}
            </div>
            <button type="button" :disabled="busy" @click="$emit('chooseLocation')">
              <FolderSearch :size="17" />
              选择
            </button>
          </div>
        </div>

        <fieldset class="form-field">
          <legend>材质识别方式</legend>
          <div class="choice-grid">
            <label :class="{ selected: selectionMode === 'ai_assisted' }">
              <input
                type="radio"
                name="selection-mode"
                value="ai_assisted"
                :checked="selectionMode === 'ai_assisted'"
                :disabled="providers.length === 0 || busy"
                @change="$emit('updateSelectionMode', 'ai_assisted')"
              />
              <strong>AI 辅助选择</strong>
              <span>{{ providers.length > 0 ? "分析候选材质" : "没有配置完成的 Provider" }}</span>
            </label>
            <label :class="{ selected: selectionMode === 'manual' }">
              <input
                type="radio"
                name="selection-mode"
                value="manual"
                :checked="selectionMode === 'manual'"
                :disabled="busy"
                @change="$emit('updateSelectionMode', 'manual')"
              />
              <strong>手动选择</strong>
              <span>模型检查后自行分类</span>
            </label>
          </div>
        </fieldset>

        <template v-if="selectionMode === 'ai_assisted'">
          <label class="form-field">
            <span>模型 Provider</span>
            <select
              :value="providerId"
              :disabled="busy"
              @change="$emit('updateProviderId', ($event.target as HTMLSelectElement).value)"
            >
              <option v-for="provider in providers" :key="provider.provider_id" :value="provider.provider_id">
                {{ provider.display_name }} · {{ provider.default_model }}
              </option>
            </select>
          </label>
          <label class="toggle-row">
            <span>
              <strong>自动确认高置信材质</strong>
              <small>关闭后先进入材质确认。</small>
            </span>
            <input
              type="checkbox"
              :checked="confirmationMode === 'automatic'"
              :disabled="busy"
              @change="
                $emit(
                  'updateConfirmationMode',
                  ($event.target as HTMLInputElement).checked ? 'automatic' : 'manual',
                )
              "
            />
          </label>
        </template>

        <div v-if="!managementAvailable" class="dialog-warning" role="alert">
          当前服务为只读模式，无法导入本地 FBX。
        </div>
        <div v-if="errorMessage" class="dialog-error" role="alert">{{ errorMessage }}</div>
      </div>

      <footer>
        <button class="secondary-button" type="button" :disabled="busy" @click="$emit('close')">
          取消
        </button>
        <button class="primary-button" type="button" :disabled="!canSubmit || busy" @click="$emit('submit')">
          <LoaderCircle v-if="busy" :size="17" class="spinning" />
          {{ stageLabel[stage] }}
        </button>
      </footer>
    </section>
  </div>
</template>
