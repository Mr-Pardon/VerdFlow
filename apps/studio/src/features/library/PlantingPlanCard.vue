<script setup lang="ts">
import { ArrowUpRight, Check, Trees } from "@lucide/vue";
import type { RoleBindingProfileSummary } from "../../api";
import LibraryThumbnail from "./LibraryThumbnail.vue";

defineProps<{
  plan: RoleBindingProfileSummary;
  strategyName?: string;
  selected?: boolean;
  unavailable?: string;
  disabled?: boolean;
  selectable?: boolean;
}>();
defineEmits<{ choose: [] }>();
</script>

<template>
  <button
    class="planting-plan-card"
    type="button"
    :class="{ selected, unavailable }"
    :disabled="disabled"
    :aria-pressed="selectable ? Boolean(selected) : undefined"
    @click="$emit('choose')"
  >
    <span class="plan-card-title"
      ><strong>{{ plan.display_name }}</strong>
      <Check v-if="selected" :size="18" /><ArrowUpRight v-else :size="17" />
    </span>
    <span class="plan-card-strategy"
      >{{ strategyName || plan.strategy_id }}
      <small :title="plan.resolved_strategy_version && plan.resolved_strategy_version !== plan.strategy_version
        ? `保存于 v${plan.strategy_version}，当前使用兼容版本` : undefined"
      >v{{ plan.resolved_strategy_version ?? plan.strategy_version }}</small></span
    >
    <span class="plan-card-plants">
      <span
        v-for="(plant, index) in (plan.plants ?? []).slice(0, 4)"
        :key="`${plant.asset_id}:${index}`"
        class="plan-card-plant"
      >
        <LibraryThumbnail
          :resource-id="plant.thumbnail_resource_id"
          :alt="plant.display_name"
          ><Trees :size="24"
        /></LibraryThumbnail>
      </span>
      <span
        v-if="!plan.plants?.length"
        class="plan-card-plant plan-card-placeholder"
        ><Trees :size="28"
      /></span>
      <span v-if="(plan.asset_count ?? 0) > 4" class="plan-card-more"
        >+{{ (plan.asset_count ?? 0) - 4 }}</span
      >
    </span>
    <span class="plan-card-plant-names">{{
      plan.plants?.map((plant) => plant.display_name).join(" · ") || "植被搭配"
    }}</span>
    <span class="plan-card-meta"
      ><span
        >{{ plan.enabled_role_count ?? "-" }} 项用途 ·
        {{ plan.asset_count ?? "-" }} 种植被</span
      >
      <span>{{
        plan.has_parameter_preset ? "已保存效果" : "默认效果"
      }}</span></span
    >
    <span v-if="unavailable" class="plan-card-warning">{{ unavailable }}</span>
  </button>
</template>

<style scoped>
.planting-plan-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  min-width: 0;
  padding: 20px;
  text-align: left;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  color: var(--color-text);
  background: var(--color-surface, #fff);
  cursor: pointer;
}
.planting-plan-card:hover:not(:disabled),
.planting-plan-card.selected {
  border-color: var(--color-accent);
}
.planting-plan-card.selected {
  background: var(--color-accent-soft);
}
.planting-plan-card:disabled {
  cursor: wait;
  opacity: 0.65;
}
.plan-card-title {
  display: flex;
  gap: 12px;
  align-items: start;
  justify-content: space-between;
}
.plan-card-title strong {
  font-size: 16px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.plan-card-title svg {
  flex-shrink: 0;
  color: var(--color-accent);
  margin-top: 3px;
}
.plan-card-strategy {
  color: var(--color-text-muted);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.plan-card-strategy small {
  margin-left: 6px;
  font-size: 11px;
}
.plan-card-plants {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 76px;
}
.plan-card-plant {
  display: grid;
  place-items: center;
  width: 68px;
  height: 76px;
  min-width: 0;
  border-radius: 4px;
  overflow: hidden;
  background: #f1f5f4;
  color: var(--color-accent);
}
.plan-card-plant :deep(img) {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.plan-card-plant :deep(> span) {
  display: grid;
  width: 100%;
  height: 100%;
  place-items: center;
}
.plan-card-more {
  color: var(--color-text-muted);
  font-size: 12px;
  margin-left: auto;
}
.plan-card-plant-names {
  min-height: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--color-text-muted);
}
.plan-card-meta {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 6px;
  border-top: 1px solid var(--color-border);
  padding-top: 12px;
  margin-top: auto;
  color: var(--color-text-muted);
  font-size: 11px;
}
.plan-card-warning {
  color: #a05d21;
  font-size: 11px;
}
</style>
