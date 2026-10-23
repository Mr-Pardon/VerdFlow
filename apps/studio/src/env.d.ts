/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_VERDFLOW_API_BASE_URL?: string;
  readonly VITE_VERDFLOW_API_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  readonly verdflowPlatform?: import("./platform").DesktopPlatformApi;
}
