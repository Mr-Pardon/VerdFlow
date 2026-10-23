<script setup lang="ts">
import ServiceStatus from "./ServiceStatus.vue";
import SideNavigation from "./SideNavigation.vue";
import type { ConnectionState } from "../features/projects/useProjects";
import { provide } from "vue";
import { Download } from "@lucide/vue";
import { applicationUpdatesKey, useApplicationUpdates } from "../features/settings/useApplicationUpdates";

const updates = useApplicationUpdates();
provide(applicationUpdatesKey, updates);

const logoUrl = `${import.meta.env.BASE_URL}brand/logo-light.png`;

defineProps<{
  connectionState: ConnectionState;
  guideStep?: string;
}>();
</script>

<template>
  <div class="app-shell" :data-guide-step="guideStep">
    <header class="app-header">
      <a class="brand" href="https://mr-pardon.github.io/verdflow-site/" target="_blank" rel="noopener noreferrer" title="打开 VerdFlow 用户手册" aria-label="VerdFlow 用户手册（在浏览器中打开）">
        <img class="brand__logo" :src="logoUrl" alt="VerdFlow" width="141" height="28" :draggable="false" />
      </a>
      <div class="app-header__status">
        <RouterLink v-if="updates.attention.value" class="app-update-notice" to="/settings" :title="updates.label.value"><Download :size="15" /><span>{{ updates.state.value?.stage === 'ready' ? '更新已就绪' : '版本更新' }}</span></RouterLink>
        <ServiceStatus :state="connectionState" />
      </div>
    </header>
    <aside class="app-sidebar">
      <SideNavigation />
    </aside>
    <main class="app-main">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-header__status { display: flex; align-items: center; gap: 18px; min-width: 0; }
.app-update-notice { display: inline-flex; align-items: center; gap: 6px; color: #138c85; font-size: 13px; text-decoration: none; white-space: nowrap; }
@media (max-width: 520px) { .app-header__status { gap: 8px; } .app-update-notice span { display: none; } }
</style>
