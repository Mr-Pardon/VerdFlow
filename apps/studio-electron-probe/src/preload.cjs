const { contextBridge, ipcRenderer, webUtils } = require("electron");

const channels = Object.freeze({
  updateStatus: "verdflow:update-status",
  updateCheck: "verdflow:update-check",
  updateDownload: "verdflow:update-download",
  updateCancel: "verdflow:update-cancel",
  updatePreferences: "verdflow:update-preferences",
  updateApply: "verdflow:update-apply",
  updateState: "verdflow:update-state",
  connection: "verdflow:connection",
  request: "verdflow:request",
  selectSource: "verdflow:select-source",
  selectSources: "verdflow:select-sources",
  selectStrategyPackageDirectory: "verdflow:select-strategy-package-directory",
  selectProjectDirectory: "verdflow:select-project-directory",
  selectNewProjectDirectory: "verdflow:select-new-project-directory",
  selectExternalRuntimePath: "verdflow:select-external-runtime-path",
  selectExportTarget: "verdflow:select-export-target",
  saveExport: "verdflow:save-export",
  launchDtsExplorer: "verdflow:launch-dts-explorer",
  revealPath: "verdflow:reveal-path",
});

const platform = Object.freeze({
  applicationUpdates: Object.freeze({
    status: () => ipcRenderer.invoke(channels.updateStatus),
    check: () => ipcRenderer.invoke(channels.updateCheck),
    download: () => ipcRenderer.invoke(channels.updateDownload),
    cancel: () => ipcRenderer.invoke(channels.updateCancel),
    preferences: (value) => ipcRenderer.invoke(channels.updatePreferences, value),
    apply: () => ipcRenderer.invoke(channels.updateApply),
    subscribe: (listener) => {
      const receive = (_event, state) => listener(state);
      ipcRenderer.on(channels.updateState, receive);
      return () => ipcRenderer.removeListener(channels.updateState, receive);
    },
  }),
  getLocalApiConnection: () => ipcRenderer.invoke(channels.connection),
  requestLocalApi: (request) => ipcRenderer.invoke(channels.request, request),
  selectSourceFile: (extensions) =>
    ipcRenderer.invoke(channels.selectSource, extensions),
  selectSourceFiles: (extensions) =>
    ipcRenderer.invoke(channels.selectSources, extensions),
  resolveDroppedFilePath: async (file) => {
    const filePath = webUtils.getPathForFile(file);
    return filePath
      ? { ok: true, value: filePath }
      : { ok: false, reason: "failed", message: "无法读取拖入文件的本地路径" };
  },
  selectStrategyPackageDirectory: () =>
    ipcRenderer.invoke(channels.selectStrategyPackageDirectory),
  selectProjectDirectory: () =>
    ipcRenderer.invoke(channels.selectProjectDirectory),
  selectNewProjectDirectory: (request) =>
    ipcRenderer.invoke(channels.selectNewProjectDirectory, request),
  selectExternalRuntimePath: (request) =>
    ipcRenderer.invoke(channels.selectExternalRuntimePath, request),
  selectExportTarget: (request) =>
    ipcRenderer.invoke(channels.selectExportTarget, request),
  saveExport: (request) => ipcRenderer.invoke(channels.saveExport, request),
  launchDtsExplorer: (request) =>
    ipcRenderer.invoke(channels.launchDtsExplorer, request),
  revealPath: (path) => ipcRenderer.invoke(channels.revealPath, path),
});

contextBridge.exposeInMainWorld("verdflowPlatform", platform);
