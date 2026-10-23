import { LocalApiClient } from "../../api";
import { platformBridge } from "../../platform";

const client = new LocalApiClient(platformBridge());
const resolvedUrls = new Map<string, string>();
const pendingUrls = new Map<string, Promise<string>>();
const requestQueue: Array<() => void> = [];
const maxConcurrentRequests = 6;
let activeRequests = 0;

const drainRequestQueue = () => {
  while (activeRequests < maxConcurrentRequests) {
    const start = requestQueue.shift();
    if (!start) return;
    activeRequests += 1;
    start();
  }
};

const scheduleRequest = <Value>(operation: () => Promise<Value>): Promise<Value> =>
  new Promise<Value>((resolve, reject) => {
    requestQueue.push(() => {
      operation()
        .then(resolve, reject)
        .finally(() => {
          activeRequests -= 1;
          drainRequestQueue();
        });
    });
    drainRequestQueue();
  });

const loadResource = async (resourceId: string) => {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      return await scheduleRequest(() => client.libraryResource(resourceId));
    } catch (error) {
      lastError = error;
      if (attempt < 2) {
        await new Promise((resolve) => window.setTimeout(resolve, 120 * (attempt + 1)));
      }
    }
  }
  throw lastError;
};

export const libraryResourceUrl = (resourceId: string): Promise<string> => {
  const resolved = resolvedUrls.get(resourceId);
  if (resolved) return Promise.resolve(resolved);

  const pending = pendingUrls.get(resourceId);
  if (pending) return pending;

  const request = loadResource(resourceId)
    .then((resource) => {
      const binary = atob(resource.data_base64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      const url = URL.createObjectURL(
        new Blob([bytes], { type: resource.media_type }),
      );
      resolvedUrls.set(resourceId, url);
      return url;
    })
    .finally(() => pendingUrls.delete(resourceId));

  pendingUrls.set(resourceId, request);
  return request;
};
