<script setup lang="ts">
import { FolderSearch, LoaderCircle, X } from "@lucide/vue";

defineProps<{
  vectorPath: string;
  elevationPath: string;
  projectName: string;
  projectPath: string;
  managementAvailable: boolean;
  busy: boolean;
  canSubmit: boolean;
  stage:
    | "idle"
    | "creating_project"
    | "importing_vector"
    | "importing_elevation"
    | "assembling_sources"
    | "preparing_regions";
  errorMessage: string | null;
}>();

defineEmits<{
  close: [];
  chooseVector: [];
  chooseElevation: [];
  chooseLocation: [];
  submit: [];
  updateProjectName: [value: string];
}>();

const stageLabel = {
  idle: "创建并准备区域",
  creating_project: "正在创建项目",
  importing_vector: "正在导入区域数据",
  importing_elevation: "正在导入高程数据",
  assembling_sources: "正在建立输入组合",
  preparing_regions: "正在准备种植区域",
};
</script>

<template>
  <div class="dialog-backdrop" @mousedown.self="$emit('close')">
    <section class="creation-dialog" role="dialog" aria-modal="true" aria-labelledby="geospatial-create-title">
      <header>
        <div>
          <span>DTS · 地理空间种植</span>
          <h2 id="geospatial-create-title">创建种植项目</h2>
        </div>
        <button type="button" title="关闭" :disabled="busy" @click="$emit('close')">
          <X :size="19" />
        </button>
      </header>

      <div class="creation-dialog__body">
        <div class="form-field">
          <span>种植区域 <em>必填</em></span>
          <div class="path-picker">
            <div :title="vectorPath">
              {{ vectorPath || "选择 .shp、.geojson 或 .json 文件" }}
            </div>
            <button type="button" :disabled="busy" @click="$emit('chooseVector')">
              <FolderSearch :size="17" />
              选择
            </button>
          </div>
        </div>
        <div class="form-field">
          <span>地形高程 <em>必填</em></span>
          <div class="path-picker">
            <div :title="elevationPath">
              {{ elevationPath || "选择 .tif 或 .tiff 文件" }}
            </div>
            <button type="button" :disabled="busy" @click="$emit('chooseElevation')">
              <FolderSearch :size="17" />
              选择
            </button>
          </div>
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

        <p class="dialog-note">
          VerdFlow 按输入数值直接处理；请确保区域与 GeoTIFF 已使用相同的米制坐标。
        </p>
        <div v-if="!managementAvailable" class="dialog-warning" role="alert">
          当前服务为只读模式，无法导入本地地理数据。
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
