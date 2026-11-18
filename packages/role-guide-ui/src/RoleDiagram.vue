<script setup lang="ts">
import { Shrub, Sprout, Trees } from "@lucide/vue";
import type { PresentedRole, RoleDiagramMark } from "./types";
import { roleColor } from "./types";

defineProps<{
  marks: readonly RoleDiagramMark[];
  roles: PresentedRole[];
  activeRoleKey?: string;
  label: string;
}>();
const symbols = { tree: Trees, shrub: Shrub, groundcover: Sprout };
</script>

<template>
  <svg class="role-diagram" viewBox="0 0 100 62" role="img" :aria-label="label">
    <title>{{ label }}</title>
    <g v-for="(mark, index) in marks" :key="index" :opacity="activeRoleKey && mark.role_key !== activeRoleKey ? 0.45 : 1">
      <polygon v-if="mark.kind === 'area'" :points="mark.points.map(point => point.join(',')).join(' ')"
        :fill="roleColor(roles, mark.role_key)" fill-opacity="0.15" :stroke="roleColor(roles, mark.role_key)"
        stroke-opacity="0.45" stroke-width="0.5" stroke-linejoin="round" />
      <template v-else>
        <circle :cx="mark.x" :cy="mark.y" :r="mark.size * 0.65"
          :fill="roleColor(roles, mark.role_key)" fill-opacity="0.16" :stroke="roleColor(roles, mark.role_key)" stroke-width="0.65" />
        <component :is="symbols[mark.kind]" :x="mark.x - mark.size / 2" :y="mark.y - mark.size / 2"
          :size="mark.size" :color="roleColor(roles, mark.role_key)" :stroke-width="1.7" />
      </template>
    </g>
  </svg>
</template>

<style scoped>
.role-diagram { display: block; width: 100%; aspect-ratio: 100 / 62; }
</style>
