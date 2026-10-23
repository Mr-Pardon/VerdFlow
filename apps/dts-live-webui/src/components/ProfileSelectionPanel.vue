<script setup lang="ts">
import { ChevronUp, ChevronDown, Settings2 } from "@lucide/vue";
import { profileKey } from "../shared/presentation";
import type { LiveConfiguration } from "../features/useLiveConfiguration";
const props = defineProps<{ controller: LiveConfiguration; selectionLocked: boolean }>();
const {
  selectedProfileKey,
  loadingProfileParameters,
  selectedStrategy,
  selectedProfile,
  chooseProfile,
  mappingReady,
  openMapping,
} = props.controller;
import { ref } from "vue";
const mappingSectionExpanded = ref(true);
</script>

<template>
<section v-if="selectedStrategy" class="panel-section mapping-section">
        <button
          class="section-heading section-toggle"
          type="button"
          :aria-expanded="mappingSectionExpanded"
          @click="mappingSectionExpanded = !mappingSectionExpanded"
        >
          <div>
            <span class="section-index">02</span>
            <strong>植被搭配</strong>
          </div>
          <span class="section-heading-actions">
            <span class="mapping-state" :class="{ ready: mappingReady }">
              {{ mappingReady ? "已就绪" : "待配置" }}
            </span>
            <ChevronUp v-if="mappingSectionExpanded" :size="16" />
            <ChevronDown v-else :size="16" />
          </span>
        </button>

        <div v-if="!mappingSectionExpanded" class="section-summary">
          <Settings2 :size="16" />
          <span>{{ selectedProfile?.display_name ?? "本次自定义搭配" }}</span>
        </div>
        <label v-else class="quick-field" for="profile">
          <span>从已有方案载入</span>
          <select
            id="profile"
            :value="selectedProfileKey"
            :disabled="selectionLocked"
            @change="chooseProfile(($event.target as HTMLSelectElement).value)"
          >
            <option value="">自行搭配植被</option>
            <option
              v-for="profile in selectedStrategy.profiles"
              :key="profileKey(profile)"
              :value="profileKey(profile)"
            >
              {{ profile.display_name }}
            </option>
          </select>
        </label>
        <button
          v-if="mappingSectionExpanded"
          class="advanced-action"
          :disabled="selectionLocked || loadingProfileParameters"
          @click="openMapping"
        >
          <Settings2 :size="15" />
          {{ selectedProfile ? "调整植被搭配" : mappingReady ? "编辑植被搭配" : "选择植被" }}
        </button>
      </section>
</template>
