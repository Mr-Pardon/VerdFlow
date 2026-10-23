<script setup lang="ts">
import { Leaf } from "@lucide/vue";
import { ref, watch } from "vue";

const props = withDefaults(
  defineProps<{ src: string | null; loading?: "lazy" | "eager" }>(),
  { loading: "lazy" },
);
const failed = ref(false);
watch(
  () => props.src,
  () => {
    failed.value = false;
  },
);
</script>

<template>
  <span class="vegetation-thumbnail">
    <img
      v-if="src && !failed"
      :src="src"
      alt=""
      :loading="loading"
      decoding="async"
      @error="failed = true"
    />
    <Leaf v-else :size="24" aria-hidden="true" />
  </span>
</template>

<style scoped>
.vegetation-thumbnail {
  position: relative;
  display: grid;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  place-items: center;
  color: #6e9b80;
  border-radius: 4px;
  background: #eaf0ec;
}
.vegetation-thumbnail img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.vegetation-thumbnail > svg {
  width: 24px;
  height: 24px;
}
</style>
