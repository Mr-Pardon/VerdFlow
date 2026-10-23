<script setup lang="ts">
import { useRouter } from "vue-router";

import ProjectsView from "./ProjectsView.vue";
import { confirmAction } from "../../components/confirmDialog";
import type { ProjectCardModel } from "./models";
import { requireProjectsState } from "./state";
import { useGuidePage } from "../onboarding/context";

const state = requireProjectsState();
const router = useRouter();
useGuidePage("projects", () => ({
  status: `当前管理 ${state.projects.value.length} 个项目`,
  loading: state.loading.value,
  busy: Boolean(state.openingProjectId.value || state.deletingProjectId.value),
  error: state.errorMessage.value,
}));

const projectRoute = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/dts`;

const openProject = async (project: ProjectCardModel) => {
  const opened = await state.openProject(project);
  const projectId = opened?.projectId ?? project.projectId;
  if (projectId) {
    await router.push(projectRoute(projectId));
  }
};

const chooseProject = async () => {
  const opened = await state.chooseAndOpenProject();
  if (opened) {
    await router.push(projectRoute(opened.project_id));
  }
};

const deleteProject = async (project: ProjectCardModel) => {
  const removesFiles = project.availability === "available";
  const confirmed = await confirmAction({
    title: removesFiles ? "删除整个项目？" : "移除项目记录？",
    message: removesFiles
      ? `“${project.name}”的数据库、模型缓存、运行记录和导出文件都会从本机永久删除。此操作无法恢复。`
      : `“${project.name}”不是当前可识别的 VerdFlow 项目，只会从项目列表中移除；原目录和其中的文件不会被删除。`,
    detail: project.projectPath,
    confirmLabel: removesFiles ? "永久删除" : "移除记录",
    tone: "danger",
  });
  if (!confirmed) return;
  await state.deleteProject(project);
};
</script>

<template>
  <ProjectsView
    :projects="state.projects.value"
    :loading="state.loading.value"
    :opening-project-id="state.openingProjectId.value"
    :deleting-project-id="state.deletingProjectId.value"
    :error-message="state.errorMessage.value"
    :can-select-project-directory="state.canSelectProjectDirectory"
    @choose-project="chooseProject"
    @refresh="state.refresh"
    @open="openProject"
    @reveal="state.revealProject"
    @delete="deleteProject"
  />
</template>
