<script setup lang="ts">
import { SlidersHorizontal, Trash2 } from "@lucide/vue";
import { computed } from "vue";
import VegetationThumbnail from "./VegetationThumbnail.vue";
import { normalizeUniformScale, fixedScale, scalePercent } from "../shared/presentation";
import type { LiveConfiguration } from "../features/useLiveConfiguration";

const props = defineProps<{ controller: LiveConfiguration }>();
const { activeRoleEnabled, activeCandidates, candidateScaleMode, updateScaleMode, updateCandidate,
  removeCandidate, assetForCandidate, thumbnailFor } = props.controller;
const repeatedTargets = computed(() => {
  const hashes = activeCandidates.value.flatMap(candidate => candidate.catalog_link?.observed_target_hash ? [candidate.catalog_link.observed_target_hash] : []);
  return new Set(hashes).size < hashes.length;
});
</script>

<template>
  <section class="candidate-settings" aria-label="已选植被配置">
    <header class="candidate-head">
      <div><strong>已选植被 <span>{{ activeCandidates.length }}</span></strong><small>{{ activeRoleEnabled ? '权重合计 100%' : '当前用途已关闭' }}</small></div>
      <SlidersHorizontal :size="18" aria-hidden="true" />
    </header>
    <p v-if="repeatedTargets" class="candidate-duplicate-note" role="status">部分植被引用同一模型，权重将分别计入。</p>
    <div v-if="!activeCandidates.length" class="mapping-empty-state"><SlidersHorizontal :size="28" /><span>尚未选择植被</span></div>
    <div v-else class="candidate-list">
      <article v-for="candidate in activeCandidates" :key="candidate.asset_id" class="candidate-row" :class="{ inactive: !activeRoleEnabled }">
        <div class="candidate-identity">
          <VegetationThumbnail class="candidate-thumb" :src="thumbnailFor(assetForCandidate(candidate.asset_id))" />
          <span><strong :title="candidate.display_name_snapshot ?? assetForCandidate(candidate.asset_id)?.display_name ?? candidate.asset_id">{{ candidate.display_name_snapshot ?? assetForCandidate(candidate.asset_id)?.display_name ?? candidate.asset_id }}</strong>
            <small>{{ assetForCandidate(candidate.asset_id)?.source_catalog_name ?? candidate.catalog_link?.catalog_id ?? candidate.asset_id }}</small></span>
          <button class="candidate-remove" type="button" :title="`移除${assetForCandidate(candidate.asset_id)?.display_name ?? candidate.asset_id}`"
            :disabled="!activeRoleEnabled" @click="removeCandidate(candidate.asset_id)"><Trash2 :size="16" /></button>
        </div>
        <div class="candidate-controls">
          <label><span>权重 (%)</span>
            <input :value="Math.round(candidate.weight * 100)" type="number" min="1" :max="100 - activeCandidates.length + 1" step="1"
              :disabled="!activeRoleEnabled || activeCandidates.length === 1"
              @change="updateCandidate(candidate.asset_id, 'weight', ($event.target as HTMLInputElement).value)" />
          </label>
          <label><span>缩放方式</span>
            <select :value="candidateScaleMode(candidate)" :disabled="!activeRoleEnabled" @change="updateScaleMode(candidate, $event)">
              <option value="catalog">资产默认</option><option value="uniform_random">随机范围</option><option value="fixed">固定值</option>
            </select>
          </label>
          <label v-if="candidateScaleMode(candidate) === 'fixed'" class="candidate-scale-value"><span>缩放 (%)</span>
            <input :value="scalePercent(fixedScale(candidate.scale))" type="number" min="1" step="1" :disabled="!activeRoleEnabled"
              @input="updateCandidate(candidate.asset_id, 'fixed', ($event.target as HTMLInputElement).value)" />
          </label>
          <template v-else-if="candidateScaleMode(candidate) === 'uniform_random'">
            <label><span>最小 (%)</span><input :value="scalePercent(normalizeUniformScale(candidate.scale).min)" type="number" min="1" step="1" :disabled="!activeRoleEnabled"
              @input="updateCandidate(candidate.asset_id, 'min', ($event.target as HTMLInputElement).value)" /></label>
            <label><span>最大 (%)</span><input :value="scalePercent(normalizeUniformScale(candidate.scale).max)" type="number" min="1" step="1" :disabled="!activeRoleEnabled"
              @input="updateCandidate(candidate.asset_id, 'max', ($event.target as HTMLInputElement).value)" /></label>
          </template>
        </div>
      </article>
    </div>
  </section>
</template>
