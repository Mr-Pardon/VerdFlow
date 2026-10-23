<script setup lang="ts">
import { ArrowLeft } from "@lucide/vue";
import PlannedFeature from "../../components/PlannedFeature.vue";
</script>

<template>
  <section class="blender-home">
    <header class="page-header">
      <div><span class="page-header__eyebrow">多源工作台</span><h1>Blender</h1></div>
    </header>
    <PlannedFeature
      title="Blender 工作台规划中"
      description="面向 Blender 场景的植被种植工作流正在规划，当前版本暂未开放。"
      note="目前可在 DTS 工作台中使用 Blender 处理 FBX 模型。"
    >
      <RouterLink class="secondary-button" to="/dts"><ArrowLeft :size="16" />前往 DTS 工作台</RouterLink>
    </PlannedFeature>
  </section>
</template>

<style scoped>
.blender-home { display: flex; flex-direction: column; height: 100%; min-height: 0; padding: 30px 38px 24px; overflow-y: auto; }
@media (max-width: 700px) { .blender-home { padding: 24px 18px; } }
</style>
