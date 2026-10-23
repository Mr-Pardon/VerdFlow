<script setup lang="ts">
import { X } from "@lucide/vue";

import type { ProjectCardModel } from "./models";

defineProps<{
  project: ProjectCardModel;
}>();

defineEmits<{
  close: [];
}>();
</script>

<template>
  <aside class="project-drawer" aria-label="项目状态">
    <header>
      <div>
        <span class="project-drawer__eyebrow">项目状态</span>
        <h2>{{ project.name }}</h2>
      </div>
      <button type="button" title="关闭" aria-label="关闭" @click="$emit('close')">
        <X :size="19" />
      </button>
    </header>
    <div class="project-drawer__body">
      <section>
        <span>当前状态</span>
        <strong>{{ project.stateLabel }}</strong>
        <p v-if="project.stageLabel">{{ project.stageLabel }}</p>
      </section>
      <section>
        <span>工作台</span>
        <strong>
          {{ project.workspace === "dts" ? "DTS" : project.workspace === "blender" ? "Blender" : "未指定" }}
        </strong>
      </section>
      <section>
        <span>项目路径</span>
        <p class="project-drawer__path">{{ project.projectPath }}</p>
      </section>
      <section v-if="project.errorMessage" class="project-drawer__error">
        <span>问题</span>
        <p>{{ project.errorMessage }}</p>
      </section>
    </div>
  </aside>
</template>
