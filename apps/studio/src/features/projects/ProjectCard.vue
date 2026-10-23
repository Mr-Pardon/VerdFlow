<script setup lang="ts">
import { Clock3, Folder, MoreHorizontal, Trash2 } from "@lucide/vue";

import type { ProjectCardModel } from "./models";

defineProps<{
  project: ProjectCardModel;
  busy: boolean;
  deleting: boolean;
}>();

defineEmits<{
  primary: [project: ProjectCardModel];
  reveal: [project: ProjectCardModel];
  delete: [project: ProjectCardModel];
}>();

const dateLabel = (value: string): string => {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
};
</script>

<template>
  <article class="project-card">
    <div class="project-card__header">
      <h2 :title="project.name">{{ project.name }}</h2>
      <details class="project-menu">
        <summary title="更多操作" aria-label="更多操作">
          <MoreHorizontal :size="18" />
        </summary>
        <div class="project-menu__content">
          <button type="button" @click="$emit('reveal', project)">
            在资源管理器中显示
          </button>
          <button
            class="project-menu__danger"
            type="button"
            :disabled="deleting"
            @click="$emit('delete', project)"
          >
            <Trash2 :size="14" />{{ deleting ? "正在删除" : "删除项目" }}
          </button>
        </div>
      </details>
    </div>

    <div class="project-card__tags">
      <span>{{ project.sourceLabel }}</span>
      <span v-if="project.workspace" class="project-card__target">
        {{ project.workspace === "dts" ? "DTS" : "Blender" }}
      </span>
    </div>

    <div class="project-card__status">
      <span class="status-dot" :data-tone="project.tone" aria-hidden="true" />
      <strong>{{ project.stateLabel }}</strong>
      <span v-if="project.stageLabel">· {{ project.stageLabel }}</span>
    </div>

    <dl class="project-card__metadata">
      <div>
        <dt><Clock3 :size="15" /><span class="sr-only">最近活动</span></dt>
        <dd>{{ dateLabel(project.lastOpenedAt) }}</dd>
      </div>
      <div>
        <dt><Folder :size="15" /><span class="sr-only">项目路径</span></dt>
        <dd :title="project.projectPath">{{ project.projectPath }}</dd>
      </div>
    </dl>

    <button
      class="project-card__action"
      type="button"
      :disabled="busy || deleting"
      @click="$emit('primary', project)"
    >
      {{ deleting ? "正在删除" : busy ? "正在打开" : project.actionLabel }}
    </button>
  </article>
</template>
