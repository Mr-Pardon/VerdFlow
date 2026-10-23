import { computed, onScopeDispose, ref, shallowReactive, watch } from "vue";
import { guideSteps, guideStorageKey, initialProgress, readProgress, type GuideProgress, type GuideStepId, type GuideWorkflow } from "./steps";
import type { GuideAction, GuidePageState } from "./context";

interface GuideOptions {
  path: () => string;
  connected: () => boolean;
  navigate: (path: string) => Promise<unknown>;
  hasExistingSetup: () => Promise<boolean>;
  storage?: Pick<Storage, "getItem" | "setItem">;
}
export function useFirstUseGuide(options: GuideOptions) {
  let storage = options.storage;
  try { storage ??= window.localStorage; } catch { /* Storage may be unavailable in embedded browsers. */ }
  let saved: GuideProgress | null = null;
  try { saved = readProgress(storage?.getItem(guideStorageKey) ?? null); } catch { /* The guide still works in memory. */ }
  const progress = ref(saved ?? initialProgress());
  const surface = ref<"hidden" | "welcome" | "step" | "collapsed" | "finish">(saved?.status === "paused" ? "collapsed" : "hidden");
  const pages = shallowReactive(new Map<GuideStepId, { read: () => GuidePageState }>());
  const navigating = ref(false);
  const runningAction = ref(false);
  const error = ref("");
  let disposed = false;
  let epoch = 0;
  let bootChecked = saved !== null;
  const index = computed(() => guideSteps.findIndex(step => step.id === progress.value.step));
  const step = computed(() => guideSteps[index.value]!);
  const page = computed(() => pages.get(step.value.id)?.read());
  const onStepPage = computed(() => options.path() === step.value.route);
  const busy = computed(() => navigating.value || runningAction.value || Boolean(page.value?.busy));
  const activeStep = computed(() => surface.value === "step" && onStepPage.value ? step.value.id : undefined);
  const persist = () => { try { storage?.setItem(guideStorageKey, JSON.stringify(progress.value)); } catch { /* Session-only fallback. */ } };
  const pause = () => { epoch++; surface.value = "hidden"; persist(); };
  const dismiss = () => { epoch++; progress.value.status = "dismissed"; surface.value = "hidden"; persist(); };
  const open = () => { epoch++; bootChecked = true; surface.value = "welcome"; error.value = ""; };
  const go = async (id: GuideStepId) => {
    if (busy.value || disposed) return;
    const request = ++epoch;
    progress.value.step = id;
    progress.value.status = "paused";
    navigating.value = true;
    error.value = "";
    persist();
    try {
      await options.navigate(guideSteps.find(item => item.id === id)!.route);
      if (disposed || request !== epoch) return;
      surface.value = options.path() === step.value.route ? "step" : "collapsed";
    } catch {
      if (!disposed && request === epoch) { error.value = "未能打开页面，请重试。"; surface.value = "collapsed"; }
    } finally { if (!disposed) navigating.value = false; }
  };
  const start = async (restart = false) => {
    if (busy.value) return;
    if (restart || progress.value.status !== "paused") progress.value = initialProgress();
    await go(progress.value.step);
  };
  const next = async (skip = false) => {
    if (busy.value || !onStepPage.value) return;
    const current = step.value.id;
    progress.value.visited = [...new Set([...progress.value.visited, current])];
    progress.value.skipped = progress.value.skipped.filter(id => id !== current);
    if (skip) progress.value.skipped.push(current);
    if (index.value === guideSteps.length - 1) {
      progress.value.status = "completed";
      surface.value = "finish";
      persist();
    } else await go(guideSteps[index.value + 1]!.id);
  };
  const previous = () => index.value > 0 ? go(guideSteps[index.value - 1]!.id) : Promise.resolve();
  const setWorkflow = (workflow: GuideWorkflow) => { progress.value.workflow = workflow; persist(); };
  const registerPage = (id: GuideStepId, read: () => GuidePageState) => {
    const entry = { read };
    pages.set(id, entry);
    return () => { if (pages.get(id) === entry) pages.delete(id); };
  };
  const run = async (action: GuideAction) => {
    if (busy.value || action.disabled || !onStepPage.value || disposed) return;
    runningAction.value = true;
    error.value = "";
    try { await action.run(); }
    catch { if (!disposed) error.value = "操作未完成，请在当前页面查看详情或重试。"; }
    finally { if (!disposed) runningAction.value = false; }
  };
  watch(options.path, () => {
    if (surface.value === "step" && !onStepPage.value) surface.value = "collapsed";
  });
  watch([options.connected, options.path], async ([connected, path]) => {
    if (!connected || bootChecked || path === "/") return;
    bootChecked = true;
    if (path !== "/projects") return;
    const request = epoch;
    try {
      const existing = await options.hasExistingSetup();
      if (disposed || request !== epoch || options.path() !== "/projects") return;
      if (existing) { progress.value.status = "dismissed"; persist(); }
      else surface.value = "welcome";
    } catch { /* A failed query must not be mistaken for a new installation. */ }
  }, { immediate: true });
  onScopeDispose(() => { disposed = true; epoch++; pages.clear(); });
  return { progress, surface, index, step, page, busy, error, activeStep, onStepPage,
    pause, dismiss, open, start, next, previous, go, run, registerPage, setWorkflow };
}
export type FirstUseGuide = ReturnType<typeof useFirstUseGuide>;
