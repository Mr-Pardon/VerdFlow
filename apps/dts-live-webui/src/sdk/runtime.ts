import type { UnknownRecord } from "./protocol";

export const loadScript = (source: string) =>
  new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = source;
    script.async = false;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`无法加载 DTS SDK：${source}`));
    document.head.appendChild(script);
  });

export const settle = async (value: unknown) => (isPromise(value) ? value : value);

export const isPromise = (value: unknown): value is Promise<unknown> =>
  typeof asRecord(value)?.then === "function";

export const asRecord = (value: unknown): UnknownRecord | null =>
  value !== null && typeof value === "object" ? (value as UnknownRecord) : null;

export const readableError = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
