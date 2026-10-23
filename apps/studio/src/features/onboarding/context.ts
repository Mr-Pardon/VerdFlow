import { inject, onScopeDispose, type InjectionKey } from "vue";
import type { GuideStepId } from "./steps";
import type { FirstUseGuide } from "./useFirstUseGuide";

export interface GuideAction { label: string; run: () => unknown | Promise<unknown>; disabled?: boolean }
export interface GuidePageState {
  ready?: boolean;
  status: string;
  loading?: boolean;
  busy?: boolean;
  error?: string | null;
  actions?: GuideAction[];
  authorAction?: GuideAction;
  refresh?: () => unknown | Promise<unknown>;
}
export const firstUseGuideKey: InjectionKey<FirstUseGuide> = Symbol("first-use-guide");
export function useGuidePage(id: GuideStepId, read: () => GuidePageState) {
  const guide = inject(firstUseGuideKey, null);
  if (guide) onScopeDispose(guide.registerPage(id, read));
  return guide;
}
