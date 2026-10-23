<script setup lang="ts">
import {
  ArrowUpRight,
  BookOpen,
  Box,
  Boxes,
  FolderOpen,
  GitMerge,
  Leaf,
  LibraryBig,
  Server,
  Settings,
} from "@lucide/vue";
import { RouterLink, useRoute } from "vue-router";
import type { Component } from "vue";

const route = useRoute();

const projectItem = { label: "项目", icon: FolderOpen, to: "/projects" };

const groups: Array<{ label: string; items: Array<{ label: string; icon: Component; to: string; planned?: boolean }> }> = [
  {
    label: "多源工作台",
    items: [
      { label: "DTS", icon: Leaf, to: "/dts" },
      { label: "Blender", icon: Box, to: "/blender", planned: true },
    ],
  },
  {
    label: "基础资源",
    items: [
      { label: "资产库", icon: Boxes, to: "/assets" },
      { label: "策略库", icon: BookOpen, to: "/strategies" },
    ],
  },
  {
    label: "快捷种植",
    items: [{ label: "种植方案", icon: GitMerge, to: "/plans" }],
  },
  {
    label: "运行",
    items: [{ label: "模型服务", icon: Server, to: "/model-services" }],
  },
  {
    label: "系统",
    items: [{ label: "设置", icon: Settings, to: "/settings" }],
  },
];

const isActive = (to: string): boolean =>
  to === "/projects"
    ? route.path === "/projects"
    : route.path === to || route.path.startsWith(`${to}/`);
</script>

<template>
  <nav class="side-navigation" aria-label="主导航">
    <div class="nav-sections">
      <div class="nav-primary">
        <RouterLink
          :to="projectItem.to"
          class="nav-item"
          :class="{ 'nav-item--active': isActive(projectItem.to) }"
          :aria-current="isActive(projectItem.to) ? 'page' : undefined"
          :title="projectItem.label"
        >
          <component :is="projectItem.icon" :size="19" :stroke-width="1.8" />
          <span>{{ projectItem.label }}</span>
        </RouterLink>
      </div>
      <section v-for="group in groups" :key="group.label" class="nav-group">
        <h2>{{ group.label }}</h2>
        <RouterLink
          v-for="item in group.items"
          :key="item.label"
          :to="item.to"
          class="nav-item"
          :class="{ 'nav-item--active': isActive(item.to), 'nav-item--planned': item.planned }"
          :aria-current="isActive(item.to) ? 'page' : undefined"
          :title="item.label"
        >
          <component :is="item.icon" :size="19" :stroke-width="1.8" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </section>
    </div>
    <div class="nav-footer">
      <a
        class="nav-item nav-resource-center"
        href="https://mr-pardon.github.io/verdflow-resources/"
        target="_blank"
        rel="noopener noreferrer"
        title="打开资源中心"
        aria-label="资源中心（在浏览器中打开）"
      >
        <LibraryBig :size="19" :stroke-width="1.8" aria-hidden="true" />
        <span>资源中心</span>
        <ArrowUpRight class="nav-resource-center__arrow" :size="16" :stroke-width="1.8" aria-hidden="true" />
      </a>
    </div>
  </nav>
</template>

<style scoped>
.nav-item--planned { color: var(--color-text-faint); }
</style>
