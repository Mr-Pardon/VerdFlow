<script setup lang="ts">
import { ArrowRight, FileBox, MousePointer2 } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import DtsCreateDialog from "./DtsCreateDialog.vue";
import DtsGeospatialCreateDialog from "./DtsGeospatialCreateDialog.vue";
import DtsProjectSourceDialog from "./DtsProjectSourceDialog.vue";
import { useDtsProjectCreation } from "./useDtsProjectCreation";
import { useDtsGeospatialProjectCreation } from "./useDtsGeospatialProjectCreation";
import { requireProjectsState } from "../projects/state";
import { useGuidePage } from "../onboarding/context";

const router = useRouter();
const projectsState = requireProjectsState();
const creation = useDtsProjectCreation();
const geospatialCreation = useDtsGeospatialProjectCreation();
const sourceDialogOpen = ref(false);
useGuidePage("workbench", () => ({
  status: "本步只介绍入口，不会自动启动工作流程。",
  busy: sourceDialogOpen.value || creation.dialogOpen.value || geospatialCreation.dialogOpen.value,
}));
const entryError = computed(
  () => creation.errorMessage.value ?? geospatialCreation.errorMessage.value,
);
const projectEntryAvailable = computed(
  () =>
    creation.canSelectSource.value ||
    geospatialCreation.canSelectSources.value,
);

const recentProjects = computed(() =>
  projectsState.projects.value
    .filter((project) => project.workspace === "dts")
    .slice(0, 3),
);

const openRecent = async (project: (typeof recentProjects.value)[number]) => {
  const opened = project.sessionId ? project : await projectsState.openProject(project);
  if (opened?.projectId) {
    await router.push(`/projects/${encodeURIComponent(opened.projectId)}/dts`);
  }
};

const chooseFbxSource = async () => {
  await creation.chooseSource();
  if (creation.dialogOpen.value) sourceDialogOpen.value = false;
};

const openGeospatialDialog = () => {
  geospatialCreation.openDialog();
  sourceDialogOpen.value = false;
};

onMounted(() => {
  void creation.initialize();
  void geospatialCreation.initialize();
});
</script>

<template>
  <section class="dts-home">
    <header class="page-header">
      <div>
        <span class="page-header__eyebrow">工作台</span>
        <h1>DTS</h1>
      </div>
    </header>

    <div
      v-if="
        entryError &&
        !sourceDialogOpen &&
        !creation.dialogOpen.value &&
        !geospatialCreation.dialogOpen.value
      "
      class="inline-alert"
      role="alert"
    >
      <div>
        <strong>DTS 入口暂时不可用</strong>
        <p>{{ entryError }}</p>
      </div>
    </div>

    <div class="workflow-grid" data-guide-anchor="workbench">
      <article class="workflow-card">
        <div class="workflow-card__icon"><FileBox :size="24" /></div>
        <div>
          <span class="workflow-card__state">项目流程</span>
          <h2>DTS 项目工作台</h2>
          <p>创建或继续按项目保存的种植工作，并选择适合当前数据的区域来源。</p>
        </div>
        <button
          class="primary-button"
          type="button"
          :disabled="!projectEntryAvailable"
          :title="projectEntryAvailable ? '新建 DTS 项目' : '需要桌面 PlatformBridge'"
          @click="sourceDialogOpen = true"
        >
          新建项目
          <ArrowRight :size="17" />
        </button>
      </article>

      <article class="workflow-card">
        <div class="workflow-card__icon"><MousePointer2 :size="24" /></div>
        <div>
          <span class="workflow-card__state">实时流程</span>
          <h2>实时选区种植</h2>
          <p>通过 DTS Connector 获取场景选面、高程回传并将实例加载回当前场景。</p>
        </div>
        <button class="secondary-button" type="button" @click="$router.push('/dts/realtime')">
          进入工作台
          <ArrowRight :size="17" />
        </button>
      </article>
    </div>

    <section class="recent-dts">
      <header>
        <div>
          <h2>最近 DTS 项目</h2>
          <p>快速回到最近处理的工作区。</p>
        </div>
        <button type="button" @click="$router.push('/projects')">查看全部</button>
      </header>
      <div v-if="recentProjects.length" class="recent-dts__list">
        <button v-for="project in recentProjects" :key="project.registryId" type="button" @click="openRecent(project)">
          <span>
            <strong>{{ project.name }}</strong>
            <small>{{ project.stateLabel }}<template v-if="project.stageLabel"> · {{ project.stageLabel }}</template></small>
          </span>
          <ArrowRight :size="17" />
        </button>
      </div>
      <div v-else class="recent-dts__empty">暂无 DTS 项目</div>
    </section>

    <DtsProjectSourceDialog
      v-if="sourceDialogOpen"
      :fbx-available="creation.canSelectSource.value"
      :geospatial-available="geospatialCreation.canSelectSources.value"
      @close="sourceDialogOpen = false"
      @select-fbx="chooseFbxSource"
      @select-geospatial="openGeospatialDialog"
    />

    <DtsCreateDialog
      v-if="creation.dialogOpen.value"
      :source-path="creation.sourcePath.value"
      :project-name="creation.projectName.value"
      :project-path="creation.projectPath.value"
      :selection-mode="creation.selectionMode.value"
      :confirmation-mode="creation.confirmationMode.value"
      :provider-id="creation.providerId.value"
      :providers="creation.configuredProviders.value"
      :management-available="creation.managementAvailable.value"
      :busy="creation.busy.value"
      :can-submit="creation.canSubmit.value"
      :stage="creation.stage.value"
      :error-message="creation.errorMessage.value"
      @close="creation.closeDialog"
      @choose-location="creation.chooseProjectLocation"
      @submit="creation.submit"
      @update-project-name="creation.projectName.value = $event"
      @update-selection-mode="creation.setSelectionMode"
      @update-confirmation-mode="creation.confirmationMode.value = $event"
      @update-provider-id="creation.providerId.value = $event"
    />

    <DtsGeospatialCreateDialog
      v-if="geospatialCreation.dialogOpen.value"
      :vector-path="geospatialCreation.vectorPath.value"
      :elevation-path="geospatialCreation.elevationPath.value"
      :project-name="geospatialCreation.projectName.value"
      :project-path="geospatialCreation.projectPath.value"
      :management-available="geospatialCreation.managementAvailable.value"
      :busy="geospatialCreation.busy.value"
      :can-submit="geospatialCreation.canSubmit.value"
      :stage="geospatialCreation.stage.value"
      :error-message="geospatialCreation.errorMessage.value"
      @close="geospatialCreation.closeDialog"
      @choose-vector="geospatialCreation.chooseVectorSource"
      @choose-elevation="geospatialCreation.chooseElevationSource"
      @choose-location="geospatialCreation.chooseProjectLocation"
      @submit="geospatialCreation.submit"
      @update-project-name="geospatialCreation.projectName.value = $event"
    />
  </section>
</template>
