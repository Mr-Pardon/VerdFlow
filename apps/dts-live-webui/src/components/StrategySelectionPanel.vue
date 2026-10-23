<script setup lang="ts">
import { ChevronUp, ChevronDown, Trees, LoaderCircle, Check } from "@lucide/vue";
import type { LiveConfiguration } from "../features/useLiveConfiguration";
const props = defineProps<{
  controller: LiveConfiguration;
  loading: boolean;
  selectionLocked: boolean;
  operationActive: boolean
  initiallyCollapsed?: boolean;
}>();
const {
  selectedStrategyHash,
  selectedStrategy,
  strategyFamilies,
  selectedStrategyVersions,
  chooseStrategy,
  chooseStrategyHash,
} = props.controller;
import { ref } from "vue";
const strategySectionExpanded = ref(!props.initiallyCollapsed);
</script>

<template>
<section class="panel-section strategy-section">
        <button
          class="section-heading section-toggle"
          type="button"
          :aria-expanded="strategySectionExpanded"
          @click="strategySectionExpanded = !strategySectionExpanded"
        >
          <div>
            <span class="section-index">01</span>
            <strong>种植策略</strong>
          </div>
          <span class="section-heading-actions">
            <span v-if="selectionLocked" class="locked-label">本次已锁定</span>
            <span v-else-if="selectedStrategy" class="mapping-state ready">已选择</span>
            <ChevronUp v-if="strategySectionExpanded" :size="16" />
            <ChevronDown v-else :size="16" />
          </span>
        </button>
        <div v-if="!strategySectionExpanded && selectedStrategy" class="section-summary">
          <Trees :size="16" />
          <span>{{ selectedStrategy.summary.display_name }}</span>
        </div>
        <div v-else-if="strategySectionExpanded && loading" class="empty-state compact">
          <LoaderCircle class="spin" :size="18" /> 正在读取策略
        </div>
        <div v-else-if="strategySectionExpanded && strategyFamilies.length === 0" class="empty-state compact">
          没有可执行的已信任策略
        </div>
        <template v-else-if="strategySectionExpanded">
          <div
            class="strategy-list"
            tabindex="0"
            aria-label="可滚动的种植策略列表"
          >
            <button
              v-for="family in strategyFamilies"
              :key="family.strategyId"
              class="strategy-row"
              :class="{ selected: selectedStrategy?.summary.strategy_id === family.strategyId }"
              :disabled="selectionLocked"
              @click="chooseStrategy(family.selected)"
            >
              <span class="radio-mark">
                <Check v-if="selectedStrategy?.summary.strategy_id === family.strategyId" :size="13" />
              </span>
              <span class="strategy-icon" aria-hidden="true">
                <Trees :size="24" :stroke-width="1.6" />
              </span>
              <span class="strategy-copy">
                <strong>{{ family.selected.summary.display_name }}</strong>
                <small>{{ family.selected.description }}</small>
              </span>
              <span class="version">v{{ family.selected.summary.strategy_version }}</span>
            </button>
          </div>
          <label v-if="selectedStrategyVersions.length > 1" class="quick-field strategy-version-field" for="strategy-version">
            <span>策略版本</span>
            <select
              id="strategy-version"
              :value="selectedStrategyHash"
              :disabled="selectionLocked"
              @change="chooseStrategyHash(($event.target as HTMLSelectElement).value)"
            >
              <option
                v-for="strategy in selectedStrategyVersions"
                :key="strategy.summary.package_hash"
                :value="strategy.summary.package_hash"
              >
                v{{ strategy.summary.strategy_version }}
              </option>
            </select>
          </label>
        </template>
      </section>
</template>
