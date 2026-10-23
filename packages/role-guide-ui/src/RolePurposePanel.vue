<script setup lang="ts">
import { Shrub, Sprout, Trees } from "@lucide/vue";
import { computed } from "vue";
import { roleColor } from "./types";
import type { PresentedRole } from "./types";

const props = defineProps<{ role: PresentedRole; roles: PresentedRole[] }>();
const symbols = { tree: Trees, shrub: Shrub, groundcover: Sprout };
const legend = computed(() => props.roles.filter((role) =>
  props.role.guide?.diagram.some((mark) => mark.role_key === role.role_key),
));
</script>

<template>
  <section v-if="role.guide" class="role-purpose-panel" :aria-label="`${role.display_name}的搭配建议`">
    <figure v-if="role.guide.diagram.length">
      <svg viewBox="0 0 100 62" role="img" :aria-label="`${role.display_name}的位置示意`">
        <title>{{ role.display_name }}的位置示意</title>
        <template v-for="(mark, index) in role.guide.diagram" :key="index">
          <polygon v-if="mark.kind === 'area'" :points="mark.points.map((point) => point.join(',')).join(' ')"
            :fill="roleColor(roles, mark.role_key)" fill-opacity="0.15" :stroke="roleColor(roles, mark.role_key)"
            stroke-opacity="0.45" stroke-width="0.5" stroke-linejoin="round" />
          <g v-else :opacity="mark.role_key === role.role_key ? 1 : 0.45">
            <circle :cx="mark.x" :cy="mark.y" :r="mark.size * 0.65"
              :fill="roleColor(roles, mark.role_key)" :fill-opacity="mark.role_key === role.role_key ? 0.16 : 0.08"
              :stroke="roleColor(roles, mark.role_key)" :stroke-width="mark.role_key === role.role_key ? 0.65 : 0.3" />
            <component :is="symbols[mark.kind]" :x="mark.x - mark.size / 2" :y="mark.y - mark.size / 2"
              :size="mark.size" :color="roleColor(roles, mark.role_key)" :stroke-width="1.7" />
          </g>
        </template>
      </svg>
      <figcaption>构图示意</figcaption>
      <div class="role-purpose-legend">
        <span v-for="entry in legend" :key="entry.role_key" :class="{ active: entry.role_key === role.role_key }">
          <i :style="{ backgroundColor: roleColor(roles, entry.role_key) }" />{{ entry.display_name }}
        </span>
      </div>
    </figure>
    <div class="role-purpose-copy">
      <span class="role-purpose-label">推荐植被</span>
      <strong>{{ role.guide.suggested_assets.join('、') }}</strong>
      <p v-if="role.guide.note">{{ role.guide.note }}</p>
    </div>
  </section>
</template>

<style scoped>
.role-purpose-panel { display: grid; grid-template-columns: minmax(140px, 210px) minmax(0, 1fr); align-items: center; gap: 20px; margin: 14px 0; padding: 14px 0; border-block: 1px solid var(--color-border, #d9e1df); color: var(--color-text, #182320); }
.role-purpose-panel figure { min-width: 0; margin: 0; }
.role-purpose-panel figure > svg { display: block; width: 100%; aspect-ratio: 100 / 62; }
.role-purpose-panel figcaption { margin-top: 4px; text-align: center; color: var(--color-text-faint, #899590); font-size: 10px; }
.role-purpose-legend { display: flex; flex-wrap: wrap; justify-content: center; gap: 6px 10px; margin-top: 7px; }
.role-purpose-legend > span { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; color: var(--color-text-muted, #66736f); }
.role-purpose-legend > span.active { color: var(--color-text, #182320); font-weight: 650; }
.role-purpose-legend i { width: 6px; height: 6px; border-radius: 50%; }
.role-purpose-copy { min-width: 0; }
.role-purpose-label { display: block; margin-bottom: 7px; font-size: 11px; color: var(--color-text-muted, #66736f); }
.role-purpose-copy > strong { display: block; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.role-purpose-copy > p { margin: 7px 0 0; font-size: 11px; line-height: 1.7; color: var(--color-text-muted, #66736f); overflow-wrap: anywhere; }
@media (max-width: 600px) { .role-purpose-panel { grid-template-columns: minmax(0, 1fr); gap: 12px; } .role-purpose-panel figure { width: min(100%, 230px); margin-inline: auto; } }
</style>
