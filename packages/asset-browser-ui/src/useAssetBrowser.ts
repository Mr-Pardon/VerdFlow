import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import type { BrowserAsset, BrowserCatalog, AssetSearchPage, AssetSearchRequest } from "./types";

export interface AssetBrowserOptions<T extends BrowserAsset> {
  catalogs: BrowserCatalog[];
  targetType: string;
  providerId?: string;
  initialCatalogIds: string[];
  selectedIds: string[];
  search: (request: AssetSearchRequest, signal: AbortSignal) => Promise<AssetSearchPage<T>>;
  mapAsset?: (asset: T) => T;
}

export function useAssetBrowser<T extends BrowserAsset>(props: AssetBrowserOptions<T>) {
  const root = ref<HTMLElement | null>(null);
  const grid = ref<HTMLElement | null>(null);
  const scopeOpen = ref(false);
  const chosenProvider = ref("");
  const providers = computed(() => [...new Set(props.catalogs.filter(c => c.target_type === props.targetType).map(c => c.provider_id))]);
  const provider = computed(() => props.providerId || chosenProvider.value || providers.value[0] || "");
  const compatible = computed(() => props.catalogs.filter(c => c.target_type === props.targetType && c.provider_id === provider.value));
  const scope = ref<string[]>([]);
  const query = ref("");
  const category = ref("");
  const offset = ref(0);
  const pageSize = ref(48);
  const result = shallowRef<AssetSearchPage<T> | null>(null);
  const busy = ref(false);
  const error = ref("");
  const selected = computed(() => new Set(props.selectedIds));
  const items = computed(() => (result.value?.items ?? []).map(item => props.mapAsset?.(item) ?? item));
  const categoryKey = (catalogId: string, collectionId: string) => JSON.stringify([catalogId, collectionId]);
  const groups = computed(() => compatible.value.map(c => ({ ...c, collections: (result.value?.collections ?? []).filter(item => item.catalog_id === c.catalog_id) })).filter(c => c.collections.length));
  const scopeLabel = computed(() => scope.value.length === 1 ? compatible.value.find(c => c.catalog_id === scope.value[0])?.display_name ?? "1 个资产库" : `资产库 ${scope.value.length} / ${compatible.value.length}`);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let epoch = 0;
  let observer: ResizeObserver | undefined;
  let mounted = false;
  const storageKey = () => `verdflow.asset-browser.scope.v1:${props.targetType}:${provider.value}`;
  function remember() {
    try { localStorage.setItem(storageKey(), JSON.stringify(scope.value)); } catch { /* Storage can be disabled by the embedding host. */ }
  }
  function initializeScope() {
    let saved: unknown;
    try { saved = JSON.parse(localStorage.getItem(storageKey()) ?? "null"); } catch { saved = null; }
    const available = new Set(compatible.value.map(c => c.catalog_id));
    const validSaved = Array.isArray(saved) && saved.every(id => typeof id === "string");
    const preferred = (validSaved ? saved as string[] : props.initialCatalogIds).filter(id => available.has(id));
    scope.value = preferred.length || (validSaved && (saved as string[]).length === 0) ? preferred : [...available];
    category.value = "";
  }
  watch(() => `${props.targetType}:${provider.value}`, initializeScope, { immediate: true });
  watch(() => compatible.value.map(c => c.catalog_id).join("\n"), () => {
    scope.value = scope.value.filter(id => compatible.value.some(c => c.catalog_id === id));
    category.value = "";
  });
  function setScope(ids: string[]) {
    scope.value = ids;
    if (category.value && !ids.includes(JSON.parse(category.value)[0])) category.value = "";
    remember();
  }
  function toggleScope(id: string) {
    setScope(scope.value.includes(id) ? scope.value.filter(value => value !== id) : [...scope.value, id]);
  }
  function invalidate() {
    epoch++;
    clearTimeout(timer);
    controller?.abort();
    controller = undefined;
  }
  function schedule() {
    invalidate();
    offset.value = 0;
    result.value = null;
    error.value = "";
    busy.value = Boolean(scope.value.length && provider.value);
    if (mounted) timer = setTimeout(() => { void load(); }, 180);
  }
  watch([query, scope, category, pageSize, provider], schedule);
  async function load(pageOffset = 0) {
    invalidate();
    if (!mounted || !scope.value.length || !provider.value) { result.value = null; busy.value = false; return; }
    const current = epoch;
    controller = new AbortController();
    busy.value = true;
    error.value = "";
    const [catalogId, collectionId] = category.value ? JSON.parse(category.value) : [];
    try {
      const page = await props.search({ target_type: props.targetType, provider_id: provider.value,
        catalog_ids: [...scope.value], query: query.value.trim(), offset: pageOffset, limit: pageSize.value,
        ...(collectionId ? { collection_catalog_id: catalogId, collection_id: collectionId } : {}),
        ...(pageOffset && result.value ? { revision: result.value.revision } : {}),
      }, controller.signal);
      if (epoch !== current) return;
      result.value = page;
      offset.value = page.offset;
      grid.value?.scrollTo({ top: 0 });
    } catch (cause) {
      if (epoch === current) { error.value = cause instanceof Error ? cause.message : "植被加载失败"; result.value = null; }
    } finally {
      if (epoch === current) busy.value = false;
    }
  }
  function outside(event: PointerEvent) { if (!root.value?.contains(event.target as Node)) scopeOpen.value = false; }
  onMounted(() => {
    mounted = true;
    document.addEventListener("pointerdown", outside);
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => {
        if (!grid.value || grid.value.clientWidth === 0) return;
        const columns = getComputedStyle(grid.value).gridTemplateColumns.split(" ").length;
        pageSize.value = Math.min(192, Math.ceil(48 / columns) * columns);
      });
      if (grid.value) observer.observe(grid.value);
    }
    void load();
  });
  onBeforeUnmount(() => {
    mounted = false;
    invalidate();
    observer?.disconnect();
    document.removeEventListener("pointerdown", outside);
  });

  return { root, grid, scopeOpen, chosenProvider, providers, provider, compatible, scope, query, category,
    offset, pageSize, result, busy, error, selected, items, categoryKey, groups, scopeLabel, setScope, toggleScope, load };
}
