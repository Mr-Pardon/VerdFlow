<script setup lang="ts">
import { Check } from "@lucide/vue";
import { computed } from "vue";
import { roleColor, roleGroups, withRoleGroupEnabled } from "./types";
import type { PresentedRole } from "./types";

const props = withDefaults(
  defineProps<{
    roles: PresentedRole[];
    activeRoleKey: string;
    enabled?: Record<string, boolean>;
    counts?: Record<string, number>;
    disabled?: boolean;
    vertical?: boolean;
  }>(),
  { enabled: () => ({}), counts: () => ({}) },
);
const emit = defineEmits<{
  select: [roleKey: string];
  "update:enabled": [value: Record<string, boolean>];
}>();
const groups = computed(() => roleGroups(props.roles));
const groupCount = (roles: PresentedRole[]) =>
  roles.filter((role) => props.enabled[role.role_key] !== false).length;
const groupLocked = (roles: PresentedRole[]) =>
  groupCount(roles) === roles.length &&
  groupCount(props.roles) === roles.length;
const toggleGroup = (roles: PresentedRole[], event: Event) => {
  const checked = (event.target as HTMLInputElement).checked;
  if (
    props.disabled ||
    (!checked && groupCount(props.roles) <= groupCount(roles))
  )
    return;
  emit(
    "update:enabled",
    withRoleGroupEnabled(
      props.roles,
      props.enabled,
      roles.map((role) => role.role_key),
      checked,
    ),
  );
};
</script>

<template>
  <nav
    class="role-choice-nav"
    :class="{ vertical, grouped: groups.length > 1 }"
    aria-label="植被用途"
  >
    <section v-for="group in groups" :key="group.id" class="role-choice-group">
      <header v-if="group.name || groups.length > 1">
        <div class="role-group-heading">
          <strong>{{ group.name || "植被用途" }}</strong>
          <label
            :title="
              groupLocked(group.roles)
                ? '至少保留一项启用的植被用途'
                : '启用或关闭此分类'
            "
          >
            <input
              type="checkbox"
              :checked="groupCount(group.roles) === group.roles.length"
              :indeterminate="
                groupCount(group.roles) > 0 &&
                groupCount(group.roles) < group.roles.length
              "
              :aria-label="`启用${group.name || '植被用途'}分类`"
              :disabled="disabled || groupLocked(group.roles)"
              @change="toggleGroup(group.roles, $event)"
            />{{ groupCount(group.roles) }}/{{ group.roles.length }}
          </label>
        </div>
        <span v-if="group.description">{{ group.description }}</span>
      </header>
      <div class="role-choice-grid">
        <button
          v-for="role in group.roles"
          :key="role.role_key"
          type="button"
          :class="{
            active: activeRoleKey === role.role_key,
            off: enabled[role.role_key] === false,
          }"
          :aria-pressed="activeRoleKey === role.role_key"
          :disabled="disabled"
          @click="emit('select', role.role_key)"
        >
          <span class="role-choice-heading">
            <i :style="{ backgroundColor: roleColor(roles, role.role_key) }" />
            <strong>{{ role.display_name }}</strong>
            <Check
              v-if="enabled[role.role_key] !== false && counts[role.role_key]"
              :size="14"
            />
          </span>
          <span class="role-choice-description">{{ role.description }}</span>
          <small>{{
            enabled[role.role_key] === false
              ? "已关闭"
              : counts[role.role_key]
                ? `${counts[role.role_key]} 种植被`
                : role.required === false
                  ? "可选"
                  : "待配置"
          }}</small>
        </button>
      </div>
    </section>
  </nav>
</template>

<style scoped>
.role-choice-nav {
  display: grid;
  gap: 16px;
  min-width: 0;
  color: var(--color-text, #182320);
}
.role-choice-nav.grouped {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.role-choice-group {
  min-width: 0;
}
.role-choice-group > header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 12px;
  margin-bottom: 9px;
}
.role-choice-group > header > strong {
  font-size: 12px;
  font-weight: 650;
}
.role-group-heading {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}
.role-group-heading label {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--color-text-muted, #66736f);
  font-size: 11px;
  cursor: pointer;
}
.role-group-heading input {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--color-accent, #168f88);
}
.role-choice-group > header > span {
  color: var(--color-text-muted, #66736f);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.role-choice-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 160px), 1fr));
  gap: 8px;
}
.role-choice-grid > button {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
  min-height: 110px;
  padding: 12px 14px;
  text-align: left;
  color: inherit;
  border: 1px solid var(--color-border, #d9e1df);
  border-radius: 6px;
  background: var(--color-surface, #fff);
  cursor: pointer;
}
.role-choice-grid > button.active {
  border-color: var(--color-accent, #168f88);
  background: var(--color-accent-soft, #e4f3f1);
}
.role-choice-grid > button:hover:not(:disabled) {
  border-color: var(--color-accent, #168f88);
}
.role-choice-grid > button.off {
  opacity: 0.65;
}
.role-choice-grid > button:disabled {
  cursor: not-allowed;
}
.role-choice-heading {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.role-choice-heading > i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.role-choice-heading > strong {
  flex: 1;
  font-size: 13px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.role-choice-heading > svg {
  flex-shrink: 0;
  color: var(--color-accent, #168f88);
}
.role-choice-description {
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-muted, #66736f);
  overflow-wrap: anywhere;
}
.role-choice-grid small {
  margin-top: auto;
  font-size: 10px;
  color: var(--color-text-muted, #66736f);
}
.vertical .role-choice-grid {
  grid-template-columns: minmax(0, 1fr);
}
.role-choice-nav.vertical {
  grid-template-columns: minmax(0, 1fr);
}
.vertical .role-choice-grid > button {
  min-height: 100px;
}
.vertical .role-choice-group > header {
  display: block;
}
.vertical .role-choice-group > header > span {
  display: block;
  margin-top: 4px;
}
@media (max-width: 750px) {
  .role-choice-nav.grouped {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
