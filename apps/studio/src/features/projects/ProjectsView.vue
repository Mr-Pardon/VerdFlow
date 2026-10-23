<script setup lang="ts">
import { computed, ref } from "vue";
import { FolderOpen, RefreshCw, Search } from "@lucide/vue";

import ProjectCard from "./ProjectCard.vue";
import ProjectDetailDrawer from "./ProjectDetailDrawer.vue";
import type { ProjectCardModel, ProjectFilter } from "./models";
import { projectFilters } from "./useProjects";

const props = defineProps<{
  projects: ProjectCardModel[];
  loading: boolean;
  openingProjectId: string | null;
  deletingProjectId: string | null;
  errorMessage: string | null;
  canSelectProjectDirectory: boolean;
}>();

const emit = defineEmits<{
  chooseProject: [];
  open: [project: ProjectCardModel];
  refresh: [];
  reveal: [project: ProjectCardModel];
  delete: [project: ProjectCardModel];
}>();

const query = ref("");
const activeFilter = ref<ProjectFilter>("all");
const selectedProject = ref<ProjectCardModel | null>(null);

const visibleProjects = computed(() => {
  const normalized = query.value.trim().toLocaleLowerCase();
  return props.projects.filter((project) => {
    const inFilter =
      activeFilter.value === "all" || project.filter === activeFilter.value;
    const matches =
      normalized.length === 0 ||
      project.name.toLocaleLowerCase().includes(normalized) ||
      project.projectPath.toLocaleLowerCase().includes(normalized);
    return inFilter && matches;
  });
});

const primaryAction = (project: ProjectCardModel) => {
  if (project.availability !== "available") {
    selectedProject.value = project;
    return;
  }
  emit("open", project);
};
</script>

<template>
  <section class="projects-view">
    <header class="page-header">
      <div>
        <span class="page-header__eyebrow">工作台</span>
        <h1>项目</h1>
      </div>
      <div class="page-actions">
        <label class="search-control">
          <Search :size="18" aria-hidden="true" />
          <span class="sr-only">搜索项目</span>
          <input v-model="query" type="search" placeholder="搜索项目名称或路径" />
        </label>
        <button class="icon-button" type="button" title="刷新" @click="$emit('refresh')">
          <RefreshCw :size="18" :class="{ spinning: loading }" />
        </button>
        <button
          class="primary-button"
          type="button"
          :disabled="
            !canSelectProjectDirectory ||
            openingProjectId === '__selected_project__'
          "
          :title="
            canSelectProjectDirectory
              ? '打开已有项目'
              : '需要桌面 PlatformBridge'
          "
          @click="$emit('chooseProject')"
        >
          <FolderOpen :size="18" />
          <span>
            {{
              openingProjectId === "__selected_project__"
                ? "正在打开"
                : "打开已有项目"
            }}
          </span>
        </button>
      </div>
    </header>

    <div class="filter-bar" role="group" aria-label="项目状态筛选">
      <button
        v-for="filter in projectFilters"
        :key="filter.key"
        type="button"
        :class="{ active: activeFilter === filter.key }"
        @click="activeFilter = filter.key"
      >
        {{ filter.label }}
      </button>
    </div>

    <div v-if="errorMessage" class="inline-alert" role="alert">
      <div>
        <strong>项目列表暂时不可用</strong>
        <p>{{ errorMessage }}</p>
      </div>
      <button type="button" @click="$emit('refresh')">重新连接</button>
    </div>

    <div v-if="loading && projects.length === 0" class="project-grid" aria-busy="true">
      <div v-for="index in 6" :key="index" class="project-skeleton" />
    </div>

    <div v-else-if="visibleProjects.length > 0" class="project-grid">
      <ProjectCard
        v-for="project in visibleProjects"
        :key="project.registryId"
        :project="project"
        :busy="openingProjectId === project.registryId"
        :deleting="deletingProjectId === project.registryId"
        @primary="primaryAction"
        @reveal="$emit('reveal', $event)"
        @delete="$emit('delete', $event)"
      />
    </div>

    <div v-else-if="!errorMessage" class="empty-state">
      <FolderOpen :size="32" :stroke-width="1.5" />
      <strong>{{ projects.length === 0 ? "暂无最近项目" : "没有匹配的项目" }}</strong>
      <p v-if="projects.length > 0">调整搜索内容或状态筛选。</p>
    </div>

    <ProjectDetailDrawer
      v-if="selectedProject"
      :project="selectedProject"
      @close="selectedProject = null"
    />
  </section>
</template>
