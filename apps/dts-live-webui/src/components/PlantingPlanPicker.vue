<script setup lang="ts">
import {
  Check,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  Search,
  X,
} from "@lucide/vue";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import type { LiveConfiguration } from "../features/useLiveConfiguration";
import VegetationThumbnail from "./VegetationThumbnail.vue";
import { scrollPanelWheel } from "../shared/panelScroll";
import { useAdjacentPanel } from "../features/useAdjacentPanel";

const props = defineProps<{
  controller: LiveConfiguration;
  anchor: HTMLElement | null;
}>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLElement | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const query = ref("");
const strategyFilter = ref("");
const page = ref(0);
const selecting = ref("");
const error = ref("");
const { overlay, narrow, position } = useAdjacentPanel(() => props.anchor, {
  width: 560,
  height: 760,
});
const strategies = computed(() => [
  ...new Map(
    props.controller.availablePlans.value.map((item) => [
      item.strategy.summary.strategy_id,
      item.strategy.summary.display_name,
    ]),
  ).entries(),
]);
const filtered = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase();
  return props.controller.availablePlans.value.filter(
    ({ profile, strategy }) =>
      (!strategyFilter.value || profile.strategy_id === strategyFilter.value) &&
      (!needle ||
        [
          profile.display_name,
          strategy.summary.display_name,
          ...(profile.plants ?? []).map((plant) => plant.display_name),
        ].some((value) => value.toLocaleLowerCase().includes(needle))),
  );
});
const pages = computed(() =>
  Math.max(1, Math.ceil(filtered.value.length / 20)),
);
const visible = computed(() =>
  filtered.value.slice(page.value * 20, (page.value + 1) * 20),
);
watch([query, strategyFilter], () => {
  page.value = 0;
});
const outside = (event: PointerEvent) => {
  if (event.target instanceof Node && !panel.value?.contains(event.target))
    emit("close");
};
const escape = (event: KeyboardEvent) => {
  if (event.key === "Escape") {
    event.stopPropagation();
    emit("close");
  }
};
onMounted(async () => {
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("keydown", escape, true);
  await nextTick();
  searchInput.value?.focus({ preventScroll: true });
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", outside, true);
  document.removeEventListener("keydown", escape, true);
});
const choose = async (id: string) => {
  if (selecting.value || props.controller.loadingProfileParameters.value)
    return;
  selecting.value = id;
  error.value = "";
  await props.controller.choosePlan(id);
  selecting.value = "";
  if (props.controller.selectedProfile.value?.profile_id === id) emit("close");
  else error.value = "方案未能加载，请重试。";
};
</script>

<template>
  <Teleport to="body">
    <div v-if="overlay" class="live-plan-picker-scrim" aria-hidden="true"></div>
    <section
      ref="panel"
      class="live-plan-picker"
      :class="{ overlay, narrow }"
      :style="position"
      role="dialog"
      :aria-modal="overlay"
      aria-labelledby="live-plan-picker-title"
      @pointerdown.stop
      @pointermove.stop
      @pointerup.stop
      @mousedown.stop
      @mousemove.stop
      @mouseup.stop
      @click.stop
      @dblclick.stop
      @wheel="scrollPanelWheel"
      @contextmenu.stop
    >
      <header>
        <div>
          <span>快捷种植</span>
          <h2 id="live-plan-picker-title">种植方案</h2>
        </div>
        <button
          type="button"
          class="icon-button"
          title="关闭方案选择"
          @click="emit('close')"
        >
          <X :size="18" />
        </button>
      </header>
      <div class="live-plan-picker-controls">
        <label
          ><Search :size="16" /><input
            ref="searchInput"
            v-model="query"
            type="search"
            placeholder="搜索方案或植被"
            aria-label="搜索种植方案" /></label
        ><select v-model="strategyFilter" aria-label="筛选方案策略">
          <option value="">全部策略</option>
          <option v-for="[id, name] in strategies" :key="id" :value="id">
            {{ name }}
          </option>
        </select>
      </div>
      <p v-if="error" class="live-plan-picker-error" role="alert">
        {{ error }}
      </p>
      <div class="live-plan-picker-list">
        <button
          v-for="{ profile, strategy } in visible"
          :key="profile.profile_id"
          type="button"
          class="live-plan-picker-card"
          :class="{
            selected:
              controller.selectedProfile.value?.profile_id ===
              profile.profile_id,
          }"
          :aria-pressed="
            controller.selectedProfile.value?.profile_id === profile.profile_id
          "
          :disabled="
            Boolean(selecting) || controller.loadingProfileParameters.value
          "
          @click="choose(profile.profile_id)"
        >
          <span class="live-plan-card-content">
            <span class="live-plan-card-title"
              ><strong>{{ profile.display_name }}</strong
              ><LoaderCircle
                v-if="selecting === profile.profile_id"
                :size="17"
                class="spin" /><Check
                v-else-if="
                  controller.selectedProfile.value?.profile_id ===
                  profile.profile_id
                "
                :size="17"
            /></span>
            <span class="live-plan-card-strategy"
              >{{ strategy.summary.display_name }} · v{{
                profile.resolved_strategy_version ?? profile.strategy_version
              }}</span
            >
            <span class="live-plan-card-images"
              ><VegetationThumbnail
                v-for="(plant, index) in (profile.plants ?? []).slice(0, 4)"
                :key="`${plant.asset_id}:${index}`"
                :src="controller.resourceThumbnail(plant.thumbnail_resource_id)"
                loading="eager"
                :title="plant.display_name" /><VegetationThumbnail
                v-if="!profile.plants?.length"
                :src="null"
                loading="eager"
            /></span>
            <span
              class="live-plan-card-plants"
              :title="
                profile.plants?.map((plant) => plant.display_name).join(' · ')
              "
              >{{
                profile.plants
                  ?.map((plant) => plant.display_name)
                  .join(" · ") || "植被搭配"
              }}</span
            >
            <span class="live-plan-card-meta"
              ><span
                >{{ profile.enabled_role_count ?? "-" }} 项用途 ·
                {{ profile.asset_count ?? "-" }} 种植被</span
              ><span>{{
                profile.has_parameter_preset ? "已保存效果" : "默认效果"
              }}</span></span
            >
          </span>
        </button>
        <p v-if="!visible.length" class="live-plan-picker-empty">
          {{
            controller.availablePlans.value.length
              ? "没有匹配的方案"
              : "暂无可用的种植方案"
          }}
        </p>
      </div>
      <footer>
        <span>{{ filtered.length }} 个方案</span
        ><template v-if="pages > 1"
          ><button
            class="icon-button"
            type="button"
            title="上一页方案"
            :disabled="page === 0"
            @click="page--"
          >
            <ChevronLeft :size="16" /></button
          ><span>{{ page + 1 }}/{{ pages }}</span
          ><button
            class="icon-button"
            type="button"
            title="下一页方案"
            :disabled="page >= pages - 1"
            @click="page++"
          >
            <ChevronRight :size="16" /></button
        ></template>
      </footer>
    </section>
  </Teleport>
</template>
