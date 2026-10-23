<script setup lang="ts">
import { CopyPlus, Save, X, LoaderCircle } from "@lucide/vue";
import { onMounted, onBeforeUnmount, ref } from "vue";
import type { LiveConfiguration } from "../features/useLiveConfiguration";
import { scrollPanelWheel } from "../shared/panelScroll";

const props = defineProps<{ controller: LiveConfiguration }>();
const {
  profileSaveMode,
  profileName,
  editingProfile,
  savingProfile,
  selectedStrategy,
  mappingError,
  chooseProfileSaveMode,
  cancelProfileSave,
  finishMapping,
} = props.controller;
const dialog = ref<HTMLDialogElement | null>(null);
onMounted(() => dialog.value?.showModal());
onBeforeUnmount(() => dialog.value?.close());
</script>

<template>
  <dialog
    ref="dialog"
    class="mapping-save-dialog"
    aria-labelledby="mapping-save-title"
    @cancel.prevent="cancelProfileSave"
    @wheel="scrollPanelWheel"
  >
    <form @submit.prevent="finishMapping()">
      <header class="mapping-save-header">
        <div>
          <span>{{ selectedStrategy?.summary.display_name }}</span>
          <h2 id="mapping-save-title">保存种植方案</h2>
        </div>
        <button
          type="button"
          class="mapping-dialog-close"
          title="关闭保存窗口"
          :disabled="savingProfile"
          @click="cancelProfileSave"
        >
          <X :size="19" />
        </button>
      </header>
      <div class="mapping-save-body">
        <fieldset
          v-if="editingProfile"
          class="profile-save-choices"
          :disabled="savingProfile"
        >
          <legend>保存方式</legend>
          <label :class="{ selected: profileSaveMode === 'update' }">
            <input
              type="radio"
              name="profile-save-mode"
              value="update"
              :checked="profileSaveMode === 'update'"
              @change="chooseProfileSaveMode('update')"
            />
            <Save :size="18" /><span
              ><strong>更新当前方案</strong
              ><small>{{ editingProfile.display_name }}</small></span
            >
          </label>
          <label :class="{ selected: profileSaveMode === 'create' }">
            <input
              type="radio"
              name="profile-save-mode"
              value="create"
              :checked="profileSaveMode === 'create'"
              @change="chooseProfileSaveMode('create')"
            />
            <CopyPlus :size="18" /><span><strong>另存为新方案</strong></span>
          </label>
        </fieldset>
        <label class="profile-name-field"
          ><span>方案名称</span>
          <input
            v-model="profileName"
            autofocus
            maxlength="80"
            placeholder="例如：草坪铺设"
            :disabled="savingProfile"
          />
        </label>
        <p v-if="mappingError" class="mapping-inline-error" role="alert">
          {{ mappingError }}
        </p>
      </div>
      <footer class="mapping-save-footer">
        <button
          type="button"
          class="secondary-button"
          :disabled="savingProfile"
          @click="cancelProfileSave"
        >
          返回调整
        </button>
        <button type="submit" class="primary-button" :disabled="savingProfile">
          <LoaderCircle v-if="savingProfile" :size="16" class="spin" /><Save
            v-else
            :size="16"
          />
          {{
            savingProfile
              ? "正在保存"
              : profileSaveMode === "update"
                ? "更新并应用"
                : "保存并应用"
          }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
