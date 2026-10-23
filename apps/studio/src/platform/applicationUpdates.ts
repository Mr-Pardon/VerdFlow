import type { BridgeResult } from "./bridge";

export interface ApplicationUpdateState {
  supported: boolean;
  configured: boolean;
  currentVersion: string;
  stage: "idle" | "checking" | "available" | "downloading" | "preparing" | "ready" | "applying" | "error";
  latestVersion: string | null;
  progress: number;
  message: string | null;
  autoCheck: boolean;
  autoDownload: boolean;
  lastCheckedAt: string | null;
}

export interface ApplicationUpdatePreferences {
  autoCheck: boolean;
  autoDownload: boolean;
}

export interface ApplicationUpdateApi {
  status(): Promise<BridgeResult<ApplicationUpdateState>>;
  check(): Promise<BridgeResult<ApplicationUpdateState>>;
  download(): Promise<BridgeResult<ApplicationUpdateState>>;
  cancel(): Promise<BridgeResult<ApplicationUpdateState>>;
  preferences(value: ApplicationUpdatePreferences): Promise<BridgeResult<ApplicationUpdateState>>;
  apply(): Promise<BridgeResult<void>>;
  subscribe(listener: (state: ApplicationUpdateState) => void): () => void;
}
