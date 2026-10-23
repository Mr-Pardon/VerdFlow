import { shallowRef } from "vue";

export type ConfirmationTone = "default" | "danger";

export interface ConfirmationOptions {
  title: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmationTone;
}

interface ConfirmationRequest {
  options: ConfirmationOptions;
  resolve: (confirmed: boolean) => void;
}

const queue: ConfirmationRequest[] = [];
let active: ConfirmationRequest | null = null;

export const activeConfirmation = shallowRef<ConfirmationOptions | null>(null);

export const confirmAction = (
  options: ConfirmationOptions,
): Promise<boolean> =>
  new Promise((resolve) => {
    queue.push({ options, resolve });
    showNext();
  });

export const settleConfirmation = (confirmed: boolean): void => {
  if (active === null) return;
  const completed = active;
  active = null;
  activeConfirmation.value = null;
  completed.resolve(confirmed);
  queueMicrotask(showNext);
};

const showNext = (): void => {
  if (active !== null || queue.length === 0) return;
  active = queue.shift() ?? null;
  activeConfirmation.value = active?.options ?? null;
};
