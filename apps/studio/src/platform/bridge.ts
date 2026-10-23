import type { ApplicationUpdateApi } from "./applicationUpdates";

export type BridgeFailureReason = "cancelled" | "failed" | "unsupported";

export type BridgeResult<Value> =
  | { ok: true; value: Value }
  | { ok: false; reason: BridgeFailureReason; message: string };

export interface LocalApiConnection {
  baseUrl: string;
  managementAvailable: boolean;
}

export interface LocalApiRequest {
  path: string;
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: string;
}

export interface LocalApiResponse {
  status: number;
  contentType: string;
  body: string;
}

export interface SaveFileRequest {
  suggestedName: string;
  mediaType: string;
}

export interface SaveExportRequest extends SaveFileRequest {
  sessionId: string;
  exportId: string;
}

export interface NewProjectDirectoryRequest {
  suggestedName: string;
}

export interface ExternalRuntimePathRequest {
  runtimeId: string;
  displayName: string;
  pathKind: "file" | "directory";
}

export interface DtsExplorerLaunchRequest {
  executablePath: string;
  launchUrl: string;
  sessionId: string;
}

export interface DtsExplorerLaunchResult {
  processId: number | undefined;
  reused: boolean;
}

export interface PlatformCapabilities {
  selectSourceFile: boolean;
  selectSourceFiles: boolean;
  resolveDroppedFilePath: boolean;
  selectStrategyPackageDirectory: boolean;
  selectProjectDirectory: boolean;
  selectNewProjectDirectory: boolean;
  selectExportTarget: boolean;
  selectExternalRuntimePath: boolean;
  saveExport: boolean;
  revealPath: boolean;
  launchDtsExplorer: boolean;
}

export interface PlatformBridge {
  readonly applicationUpdates?: ApplicationUpdateApi;
  readonly kind: "web-dev" | "desktop";
  readonly capabilities: PlatformCapabilities;

  getLocalApiConnection(): Promise<BridgeResult<LocalApiConnection>>;
  requestLocalApi(
    request: LocalApiRequest,
  ): Promise<BridgeResult<LocalApiResponse>>;
  selectSourceFile(extensions: readonly string[]): Promise<BridgeResult<string>>;
  selectSourceFiles(extensions: readonly string[]): Promise<BridgeResult<string[]>>;
  resolveDroppedFilePath(file: File): Promise<BridgeResult<string>>;
  selectStrategyPackageDirectory(): Promise<BridgeResult<string>>;
  selectProjectDirectory(): Promise<BridgeResult<string>>;
  selectNewProjectDirectory(
    request: NewProjectDirectoryRequest,
  ): Promise<BridgeResult<string>>;
  selectExternalRuntimePath(
    request: ExternalRuntimePathRequest,
  ): Promise<BridgeResult<string>>;
  selectExportTarget(request: SaveFileRequest): Promise<BridgeResult<string>>;
  saveExport(request: SaveExportRequest): Promise<BridgeResult<string>>;
  launchDtsExplorer(
    request: DtsExplorerLaunchRequest,
  ): Promise<BridgeResult<DtsExplorerLaunchResult>>;
  revealPath(path: string): Promise<BridgeResult<void>>;
}

export interface DesktopPlatformApi {
  readonly applicationUpdates?: ApplicationUpdateApi;
  getLocalApiConnection(): Promise<BridgeResult<LocalApiConnection>>;
  requestLocalApi(
    request: LocalApiRequest,
  ): Promise<BridgeResult<LocalApiResponse>>;
  selectSourceFile(extensions: string[]): Promise<BridgeResult<string>>;
  selectSourceFiles(extensions: string[]): Promise<BridgeResult<string[]>>;
  resolveDroppedFilePath(file: File): Promise<BridgeResult<string>>;
  selectStrategyPackageDirectory(): Promise<BridgeResult<string>>;
  selectProjectDirectory(): Promise<BridgeResult<string>>;
  selectNewProjectDirectory(
    request: NewProjectDirectoryRequest,
  ): Promise<BridgeResult<string>>;
  selectExternalRuntimePath(
    request: ExternalRuntimePathRequest,
  ): Promise<BridgeResult<string>>;
  selectExportTarget(request: SaveFileRequest): Promise<BridgeResult<string>>;
  saveExport(request: SaveExportRequest): Promise<BridgeResult<string>>;
  launchDtsExplorer(
    request: DtsExplorerLaunchRequest,
  ): Promise<BridgeResult<DtsExplorerLaunchResult>>;
  revealPath(path: string): Promise<BridgeResult<void>>;
}

export const unsupported = <Value>(capability: string): BridgeResult<Value> => ({
  ok: false,
  reason: "unsupported",
  message: `当前运行环境不支持${capability}`,
});
