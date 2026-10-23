<script setup lang="ts">
import { onMounted, provide } from "vue";
import { RouterView, useRoute, useRouter } from "vue-router";
import { LocalApiClient } from "./api";
import { platformBridge } from "./platform";
import FirstUseGuide from "./features/onboarding/FirstUseGuide.vue";
import { firstUseGuideKey } from "./features/onboarding/context";
import { useFirstUseGuide } from "./features/onboarding/useFirstUseGuide";
import { hasExistingSetup } from "./features/onboarding/existingSetup";

import AppShell from "./components/AppShell.vue";
import ConfirmDialogHost from "./components/ConfirmDialogHost.vue";
import { projectsStateKey } from "./features/projects/state";
import { useProjects } from "./features/projects/useProjects";

const projectsState = useProjects();
provide(projectsStateKey, projectsState);
const route = useRoute();
const router = useRouter();
const guide = useFirstUseGuide({
  path: () => route.path,
  connected: () => projectsState.connectionState.value === "connected",
  navigate: path => router.push(path),
  hasExistingSetup: () => hasExistingSetup(new LocalApiClient(platformBridge()), () => projectsState.projects.value.length > 0),
});
provide(firstUseGuideKey, guide);

onMounted(() => {
  void projectsState.refresh();
});
</script>

<template>
  <AppShell :connection-state="projectsState.connectionState.value" :guide-step="guide.activeStep.value">
    <RouterView />
    <div v-if="projectsState.notice.value" class="toast" role="status">
      <span>{{ projectsState.notice.value }}</span>
      <button type="button" @click="projectsState.dismissNotice">关闭</button>
    </div>
  </AppShell>
  <ConfirmDialogHost />
  <FirstUseGuide :guide="guide" />
</template>
