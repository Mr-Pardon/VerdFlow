<script setup lang="ts">
import { ChevronDown, Layers, SlidersHorizontal, Sprout } from "@lucide/vue";
import { RoleDiagram, roleColor, roleGroups } from "@verdflow/role-guide-ui";
import { computed, ref } from "vue";
import type { StrategyLibraryDetail } from "../../api";
import StrategyEffectWorkspace from "../../components/StrategyEffectWorkspace.vue";

const props = defineProps<{ strategy: StrategyLibraryDetail; canPreview: boolean }>();
const groupId = ref<string>();
const roleKey = ref("");
const previewOpen = ref(true);
const values = ref<Record<string, number>>({});
const groups = computed(() => roleGroups(props.strategy.roles));
const group = computed(() => groups.value.find(item => item.id === groupId.value) ?? groups.value[0]);
const role = computed(() => group.value?.roles.find(item => item.role_key === roleKey.value));
const marks = computed(() => group.value?.roles[0]?.guide?.diagram ?? []);
const chooseGroup = (id: string) => { groupId.value = id; roleKey.value = ""; };
const updateParameter = (key: string, value: number) => { values.value = { ...values.value, [key]: value }; };
</script>

<template>
  <section v-if="group" class="composition-detail">
    <header class="composition-heading">
      <Layers :size="19" /><h3>构图与用途</h3>
    </header>
    <div v-if="groups.length > 1" class="composition-groups" role="group" aria-label="构图分组">
      <button v-for="item in groups" :key="item.id" type="button" :aria-pressed="item.id === group.id" @click="chooseGroup(item.id)">
        {{ item.name }}
      </button>
    </div>
    <div class="composition-layout" :class="{ 'no-diagram': !marks.length }">
      <figure v-if="marks.length" class="composition-figure">
        <figcaption>{{ group.name || '构图示意' }} <span>{{ role ? role.display_name : '整体示意' }}</span></figcaption>
        <RoleDiagram :marks="marks" :roles="strategy.roles" :active-role-key="role?.role_key" :label="`${group.name || '构图'}示意`" />
      </figure>
      <div class="composition-roles">
        <p v-if="group.description" class="composition-description">{{ group.description }}</p>
        <div class="composition-role-buttons" role="group" aria-label="植被用途">
          <button type="button" :aria-pressed="!role" @click="roleKey = ''"><Layers :size="15" />全部用途</button>
          <button v-for="item in group.roles" :key="item.role_key" type="button" :aria-pressed="item.role_key === roleKey" @click="roleKey = item.role_key">
            <i :style="{ backgroundColor: roleColor(strategy.roles, item.role_key) }" />{{ item.display_name }}
          </button>
        </div>
        <div class="composition-role-info">
          <article v-for="item in (role ? [role] : group.roles)" :key="item.role_key">
            <header><i :style="{ backgroundColor: roleColor(strategy.roles, item.role_key) }" /><strong>{{ item.display_name }}</strong><small>{{ item.required ? '必需' : '可选' }}</small></header>
            <p v-if="item.description">{{ item.description }}</p>
            <div v-if="role && item.guide?.suggested_assets.length" class="composition-suggestions"><Sprout :size="15" /><span>{{ item.guide.suggested_assets.join('、') }}</span></div>
            <p v-if="role && item.guide?.note">{{ item.guide.note }}</p>
          </article>
        </div>
      </div>
    </div>
    <details v-if="strategy.parameter_preview" class="composition-example" :open="previewOpen" @toggle="previewOpen = ($event.target as HTMLDetailsElement).open">
      <summary><SlidersHorizontal :size="17" /><strong>{{ strategy.generation_parameters.length ? '试调参数' : '查看效果示意' }}</strong><ChevronDown :size="16" /></summary>
      <template v-if="previewOpen">
        <p v-if="!canPreview || !strategy.summary.executable" class="composition-description">{{ !strategy.summary.executable ? '策略可执行后才能生成动态示意。' : '当前运行方式不支持动态示意。' }}</p>
        <StrategyEffectWorkspace v-else :strategy="strategy" :parameters="strategy.generation_parameters" :values="values" :enabled="{}" :random-seed="null" :show-seed="false"
          @update-parameter="updateParameter" @reset="values = {}" />
      </template>
    </details>
  </section>
</template>

<style scoped>
.composition-detail { padding: 22px; border-bottom: 1px solid var(--color-border); }
.composition-heading { display: flex; align-items: center; gap: 8px; margin-bottom: 15px; color: var(--color-accent); }
.composition-heading h3 { margin: 0; font-size: 16px; color: var(--color-text); }
.composition-groups, .composition-role-buttons { display: flex; flex-wrap: wrap; gap: 6px; }
.composition-groups { margin-bottom: 14px; }
.composition-groups button, .composition-role-buttons button { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--color-border); border-radius: 4px; background: var(--color-surface); color: var(--color-text-muted); font-size: 12px; text-align: left; cursor: pointer; }
.composition-groups button[aria-pressed='true'], .composition-role-buttons button[aria-pressed='true'] { background: var(--color-accent-soft); color: var(--color-accent); border-color: var(--color-accent); }
.composition-layout { display: grid; grid-template-columns: minmax(0, 1.12fr) minmax(0, 1fr); gap: 22px; align-items: start; }
.composition-layout.no-diagram { grid-template-columns: minmax(0, 1fr); }
.composition-figure { margin: 0; min-width: 0; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-surface-soft); }
.composition-figure figcaption { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 4px 10px; padding: 11px 13px; border-bottom: 1px solid var(--color-border); font-size: 13px; font-weight: 600; }
.composition-figure figcaption span { color: var(--color-text-muted); font-size: 11px; font-weight: 400; }
.composition-figure :deep(svg.role-diagram) { width: min(100%, 470px); margin-inline: auto; }
.composition-description { margin: 0 0 10px; font-size: 13px; line-height: 1.65; color: var(--color-text-muted); }
.composition-role-info { margin-top: 9px; }
.composition-role-info article { padding: 10px 0; border-bottom: 1px solid var(--color-border); }
.composition-role-info article header, .composition-suggestions { display: flex; align-items: center; gap: 7px; font-size: 13px; }
.composition-role-info small { margin-left: auto; flex-shrink: 0; color: var(--color-text-muted); }
.composition-role-info p { margin: 4px 0 0; font-size: 12px; line-height: 1.6; color: var(--color-text-muted); }
.composition-roles i { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }
.composition-suggestions { margin-top: 8px; color: var(--color-accent); }
.composition-example { margin-top: 18px; border-top: 1px solid var(--color-border); }
.composition-example summary { display: flex; align-items: center; gap: 8px; padding: 13px 0 7px; color: var(--color-accent); font-size: 13px; cursor: pointer; list-style: none; }
.composition-example summary::-webkit-details-marker { display: none; }
.composition-example summary svg:last-child { margin-left: auto; }
.composition-example[open] summary svg:last-child { transform: rotate(180deg); }
.composition-example[open] summary { margin-bottom: 10px; }
@media (max-width: 940px) { .composition-layout { grid-template-columns: minmax(0, 1fr); gap: 14px; } }
</style>
