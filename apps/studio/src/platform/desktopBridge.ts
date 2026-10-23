import type {
  DesktopPlatformApi,
  DtsExplorerLaunchRequest,
  ExternalRuntimePathRequest,
  LocalApiRequest,
  NewProjectDirectoryRequest,
  PlatformBridge,
  SaveFileRequest,
  SaveExportRequest,
} from "./bridge";

export const createDesktopBridge = (api: DesktopPlatformApi): PlatformBridge => ({
  kind: "desktop",
  applicationUpdates: api.applicationUpdates,
  capabilities: {
    selectSourceFile: true,
    selectSourceFiles: true,
    resolveDroppedFilePath: true,
    selectStrategyPackageDirectory: true,
    selectProjectDirectory: true,
    selectNewProjectDirectory: true,
    selectExportTarget: true,
    selectExternalRuntimePath: true,
    saveExport: true,
    revealPath: true,
    launchDtsExplorer: true,
  },
  getLocalApiConnection: () => api.getLocalApiConnection(),
  requestLocalApi: (request: LocalApiRequest) => api.requestLocalApi(request),
  selectSourceFile: (extensions: readonly string[]) =>
    api.selectSourceFile([...extensions]),
  selectSourceFiles: (extensions: readonly string[]) =>
    api.selectSourceFiles([...extensions]),
  resolveDroppedFilePath: (file: File) => api.resolveDroppedFilePath(file),
  selectStrategyPackageDirectory: () => api.selectStrategyPackageDirectory(),
  selectProjectDirectory: () => api.selectProjectDirectory(),
  selectNewProjectDirectory: (request: NewProjectDirectoryRequest) =>
    api.selectNewProjectDirectory(request),
  selectExternalRuntimePath: (request: ExternalRuntimePathRequest) =>
    api.selectExternalRuntimePath(request),
  selectExportTarget: (request: SaveFileRequest) =>
    api.selectExportTarget(request),
  saveExport: (request: SaveExportRequest) => api.saveExport(request),
  launchDtsExplorer: (request: DtsExplorerLaunchRequest) =>
    api.launchDtsExplorer(request),
  revealPath: (path: string) => api.revealPath(path),
});
