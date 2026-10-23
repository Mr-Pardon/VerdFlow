<script setup lang="ts">
import { Boxes, Plus, X } from "@lucide/vue";
import { computed, reactive } from "vue";

import type {
  AssetTargetType,
  CreateAssetCatalogInput,
} from "./useAssetLibrary";

const props = defineProps<{
  targetType: AssetTargetType;
  initialCatalogId: string;
  busy: boolean;
}>();

const emit = defineEmits<{
  close: [];
  create: [input: CreateAssetCatalogInput];
}>();

const providerOptions =
  props.targetType === "dts"
    ? [
        {
          value: "dts.static-mesh",
          label: "DTS 静态网格体",
          description: "使用静态网格体名称作为 DTS 植被定位信息。",
        },
      ]
    : [
        {
          value: "blender-local-library",
          label: "Blender 本地资产库（Collection）",
          description: "使用 Blender Asset Library 中的 Collection 资产。",
        },
      ];

const draft = reactive({
  displayName:
    props.targetType === "dts" ? "DTS 植被资产库" : "Blender 植被资产库",
  catalogId: props.initialCatalogId,
  providerId: providerOptions[0].value,
});

const catalogIdPattern = /^[a-z][a-z0-9._-]*$/;
const canCreate = computed(
  () =>
    Boolean(draft.displayName.trim()) &&
    catalogIdPattern.test(draft.catalogId.trim()) &&
    Boolean(draft.providerId.trim()),
);

const submit = () => {
  if (!canCreate.value || props.busy) return;
  emit("create", {
    targetType: props.targetType,
    catalogId: draft.catalogId.trim(),
    displayName: draft.displayName.trim(),
    providerId: draft.providerId.trim(),
  });
};
</script>

<template>
  <div class="dialog-backdrop">
    <section
      class="creation-dialog asset-catalog-create-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="新建资产库"
    >
      <header>
        <div>
          <span>{{ targetType.toUpperCase() }} CATALOG</span>
          <h2>新建资产库</h2>
        </div>
        <button type="button" title="关闭" :disabled="busy" @click="emit('close')">
          <X :size="18" />
        </button>
      </header>

      <div class="creation-dialog__body">
        <div class="asset-catalog-create-dialog__intro">
          <Boxes :size="22" />
          <p>建立可持续编辑的本地资产库，随后添加分类、植被信息和缩略图。</p>
        </div>
        <label class="form-field">
          <span>资产库名称 <em>必填</em></span>
          <input v-model="draft.displayName" type="text" autocomplete="off" />
        </label>
        <label class="form-field">
          <span>Catalog ID <em>必填</em></span>
          <input
            v-model="draft.catalogId"
            type="text"
            autocomplete="off"
            spellcheck="false"
            placeholder="例如 dts_vegetation"
          />
          <small>以小写字母开头，可使用小写字母、数字、点、短横线和下划线。</small>
        </label>
        <label class="form-field">
          <span>资产接入方式 <em>必填</em></span>
          <select v-model="draft.providerId">
            <option
              v-for="option in providerOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
          <small>{{ providerOptions.find((option) => option.value === draft.providerId)?.description }}</small>
        </label>
      </div>

      <footer>
        <button class="secondary-button" type="button" :disabled="busy" @click="emit('close')">取消</button>
        <button class="primary-button" type="button" :disabled="busy || !canCreate" @click="submit">
          <Plus :size="16" />建立并编辑
        </button>
      </footer>
    </section>
  </div>
</template>
