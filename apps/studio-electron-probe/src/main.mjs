import { createWriteStream } from "node:fs";
import { access, mkdir, rename, rm, stat } from "node:fs/promises";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  shell,
} from "electron";

import { channels } from "./channels.mjs";
import { PortableUpdater } from "./portable-updater.mjs";
import { registerUpdateHandlers } from "./update-ipc.mjs";
import { DialogPathMemory } from "./dialog-path-memory.mjs";
import { downloadLocalApiFile } from "./local-api-download.mjs";
import { manualWindowOpenHandler, USER_MANUAL_URL } from "./manual-link.mjs";
import { dailyCoreLogName, portableLayout } from "./portable-runtime.mjs";
import {
  localApiConfig,
  normalizedAbsolutePath,
  normalizedApiRequest,
  normalizedDtsExplorerLaunch,
  normalizedExtensions,
  normalizedProjectDirectoryName,
  normalizedResourceId,
  normalizedSuggestedName,
} from "./policy.mjs";

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
if (process.platform === "win32") app.setAppUserModelId("VerdFlow.Studio");
const preloadPath = path.join(currentDirectory, "preload.cjs");
const packagedDataRoot = () => {
  const override = process.env.VERDFLOW_DATA_ROOT?.trim();
  if (override) return path.resolve(override);
  const localAppData = process.env.LOCALAPPDATA?.trim();
  return path.join(localAppData ? path.resolve(localAppData) : app.getPath("appData"), "VerdFlow");
};
const portable = app.isPackaged
  ? portableLayout({
      executablePath: process.execPath,
      resourcesPath: process.resourcesPath,
      dataRootPath: packagedDataRoot(),
    })
  : null;
if (portable !== null) {
  app.setPath("userData", portable.electronDataPath);
}
const rendererDist = portable?.rendererEntryPath ??
  path.resolve(currentDirectory, "../../studio/dist/index.html");
const brandingRoot = portable !== null
  ? path.join(process.resourcesPath, "branding")
  : path.resolve(currentDirectory, "../../../assets/branding/generated/brand");
const windowIconPath = path.join(brandingRoot, process.platform === "win32" ? "app.ico" : "icon-256.png");
let localApi = localApiConfig(process.env);
const diagnosticsEnabled = process.env.VERDFLOW_DESKTOP_DIAGNOSTICS === "1";
let dtsExplorerLaunch = null;
let localApiProcess = null;
let coreLogStream = null;
let packagedShutdownStarted = false;
let dialogPathMemory = null;
let updateApplying = false;
let pendingLocalMutations = 0;
const updater = new PortableUpdater({
  portable, version: app.getVersion(), scriptRoot: currentDirectory,
  notify: (state) => {
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed()) window.webContents.send(channels.updateState, state);
    }
  },
});

const readUpdateTaskState = async (apiPath) => {
  const headers = { Accept: "application/json" };
  if (localApi.token) headers.Authorization = `Bearer ${localApi.token}`;
  const response = await fetch(new URL(apiPath, localApi.baseUrl), { headers, signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error("无法确认当前任务状态，请稍后更新");
  return response.json();
};

const rememberedDialogPaths = () => {
  dialogPathMemory ??= new DialogPathMemory({
    storagePath: path.join(app.getPath("userData"), "dialog-paths.json"),
    fallbackDirectory: app.getPath("documents"),
  });
  return dialogPathMemory;
};

const rememberDialogPath = async (key, selectedPath, selectionKind) => {
  try {
    await rememberedDialogPaths().remember(key, selectedPath, selectionKind);
  } catch (error) {
    diagnostic(`dialog path preference was not saved: ${readableError(error)}`);
  }
};

const diagnostic = (message) => {
  if (diagnosticsEnabled) {
    process.stderr.write(`[verdflow-desktop] ${message}\n`);
  }
};

const success = (value) => ({ ok: true, value });
const failure = (reason, message) => ({ ok: false, reason, message });

const startPackagedLocalApi = async () => {
  if (portable === null) return;
  await Promise.all([
    mkdir(portable.electronDataPath, { recursive: true }),
    mkdir(portable.libraryPath, { recursive: true }),
    mkdir(portable.logsPath, { recursive: true }),
  ]);
  await access(portable.pythonExecutablePath);
  const managementToken = randomBytes(32).toString("hex");
  const logPath = path.join(portable.logsPath, dailyCoreLogName());
  coreLogStream = createWriteStream(logPath, { flags: "a", encoding: "utf8" });
  coreLogStream.write(`\n[${new Date().toISOString()}] VerdFlow Core starting\n`);
  const child = spawn(
    portable.pythonExecutablePath,
    [
      "-m",
      "verdflow.local_api",
      "--library-path",
      portable.libraryPath,
      "--host",
      "127.0.0.1",
      "--port",
      "0",
    ],
    {
      cwd: portable.userDataPath,
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONUTF8: "1",
        VERDFLOW_LOCAL_API_MANAGEMENT_TOKEN: managementToken,
        VERDFLOW_SHUTDOWN_ON_STDIN_CLOSE: "1",
      },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  localApiProcess = child;
  const ready = await waitForPackagedLocalApi(child);
  if (ready.reused === true) {
    throw new Error(
      "VerdFlow 数据目录正被另一个 Core 使用，请先关闭旧进程后重试",
    );
  }
  localApi = localApiConfig({
    VERDFLOW_LOCAL_API_BASE_URL: ready.base_url,
    VERDFLOW_LOCAL_API_MANAGEMENT_TOKEN: managementToken,
  });
  await waitForLocalApiHealth(`${localApi.baseUrl}/api/v1/health`);
  diagnostic(`packaged Local API ready at ${localApi.baseUrl}`);
};

const waitForPackagedLocalApi = (child) =>
  new Promise((resolve, reject) => {
    let settled = false;
    let buffer = "";
    const timeout = setTimeout(
      () => finish(new Error("VerdFlow Core 启动超时")),
      30_000,
    );
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      error ? reject(error) : resolve(value);
    };
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      coreLogStream?.write(`[stdout] ${chunk}`);
      buffer += chunk;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const document = JSON.parse(line);
          if (document.status === "ready") finish(null, document);
          if (document.status === "error") {
            finish(new Error(document.error || "VerdFlow Core 启动失败"));
          }
        } catch {
          // Diagnostics are preserved in the local log but do not define readiness.
        }
      }
    });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) =>
      coreLogStream?.write(`[stderr] ${chunk}`),
    );
    child.once("error", finish);
    child.once("exit", (code) => {
      if (!settled) {
        finish(new Error(`VerdFlow Core 在就绪前退出 (${code})`));
      }
    });
  });

const waitForLocalApiHealth = async (url) => {
  const deadline = Date.now() + 15_000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
      if (response.ok) return;
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`VerdFlow Core 健康检查失败: ${readableError(lastError)}`);
};

const stopPackagedLocalApi = async () => {
  const child = localApiProcess;
  localApiProcess = null;
  if (child && child.exitCode === null && child.signalCode === null) {
    child.stdin?.end();
    const exited = await waitForChildExit(child, 5_000);
    if (!exited && child.pid) {
      if (process.platform === "win32") {
        spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
          stdio: "ignore",
          windowsHide: true,
        });
      } else {
        child.kill("SIGKILL");
      }
      await waitForChildExit(child, 2_000);
    }
  }
  if (coreLogStream !== null) {
    coreLogStream.write(`[${new Date().toISOString()}] VerdFlow Core stopped\n`);
    coreLogStream.end();
    coreLogStream = null;
  }
};

const waitForChildExit = (child, timeoutMs) => {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const timeout = setTimeout(() => resolve(false), timeoutMs);
    child.once("exit", () => {
      clearTimeout(timeout);
      resolve(true);
    });
  });
};

const registerHandlers = () => {
  registerUpdateHandlers({ ipcMain, BrowserWindow, dialog, updater,
    readTaskState: readUpdateTaskState, hasPendingMutations: () => pendingLocalMutations > 0,
    setApplying: value => { updateApplying = value; }, quit: () => app.quit(),
  });
  ipcMain.handle(channels.connection, () =>
    success({
      baseUrl: localApi.baseUrl,
      managementAvailable: localApi.token !== null,
    }),
  );

  ipcMain.handle(channels.request, async (_event, request) => {
    let counted = false;
    try {
      const normalized = normalizedApiRequest(request, localApi.baseUrl);
      const mutation = normalized.method !== "GET";
      if (mutation && updateApplying) return failure("failed", "更新正在准备重启，请稍后操作");
      if (mutation) { pendingLocalMutations++; counted = true; }
      const headers = { Accept: "application/json" };
      if (normalized.body !== undefined) {
        headers["Content-Type"] = "application/json";
      }
      if (localApi.token !== null) {
        headers.Authorization = `Bearer ${localApi.token}`;
      }
      const response = await fetch(normalized.target, {
        method: normalized.method,
        headers,
        body: normalized.body,
      });
      return success({
        status: response.status,
        contentType: response.headers.get("content-type") || "",
        body: await response.text(),
      });
    } catch (error) {
      return failure("failed", readableError(error));
    } finally {
      if (counted) pendingLocalMutations--;
    }
  });

  const selectSourcePaths = async (event, extensions, multiple) => {
    const normalized = normalizedExtensions(extensions);
    const assetPackage = normalized.includes("vfa");
    const profilePackage = normalized.includes("vfp");
    const strategyPackage = normalized.includes("vfs");
    const title = strategyPackage
      ? `选择${multiple ? "一个或多个" : ""} VerdFlow 策略包`
      : profilePackage
        ? `选择${multiple ? "一个或多个" : ""} VerdFlow 植被搭配包`
        : assetPackage
          ? `选择${multiple ? "一个或多个" : ""} VerdFlow 资产包`
          : "选择 Source 文件";
    const filterName = strategyPackage
      ? "VerdFlow 策略包"
      : profilePackage
        ? "VerdFlow 植被搭配包"
        : assetPackage
          ? "VerdFlow 资产包"
          : "模型文件";
    const memoryKey = `source:${normalized.join("-")}`;
    const result = await showOpenDialog(event, {
      title,
      defaultPath: await rememberedDialogPaths().defaultPath(memoryKey),
      properties: multiple ? ["openFile", "multiSelections"] : ["openFile"],
      filters: [
        {
          name: filterName,
          extensions: normalized,
        },
      ],
    });
    if (result.canceled || result.filePaths.length === 0) {
      return failure("cancelled", "已取消文件选择");
    }
    await rememberDialogPath(memoryKey, result.filePaths[0], "file");
    return success(multiple ? result.filePaths : result.filePaths[0]);
  };

  ipcMain.handle(channels.selectSource, (event, extensions) =>
    selectSourcePaths(event, extensions, false),
  );

  ipcMain.handle(channels.selectSources, (event, extensions) =>
    selectSourcePaths(event, extensions, true),
  );

  ipcMain.handle(channels.selectStrategyPackageDirectory, async (event) => {
    const result = await showOpenDialog(event, {
      title: "选择 VerdFlow 策略包目录",
      defaultPath: await rememberedDialogPaths().defaultPath("strategy-package-directory"),
      properties: ["openDirectory"],
    });
    if (result.canceled || result.filePaths.length === 0) {
      return failure("cancelled", "已取消策略包选择");
    }
    await rememberDialogPath("strategy-package-directory", result.filePaths[0], "directory");
    return success(result.filePaths[0]);
  });

  ipcMain.handle(channels.selectProjectDirectory, async (event) => {
    const result = await showOpenDialog(event, {
      title: "选择 VerdFlow 项目目录",
      defaultPath: await rememberedDialogPaths().defaultPath("project-directory"),
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || result.filePaths.length === 0) {
      return failure("cancelled", "已取消目录选择");
    }
    await rememberDialogPath("project-directory", result.filePaths[0], "directory");
    return success(result.filePaths[0]);
  });

  ipcMain.handle(channels.selectNewProjectDirectory, async (event, request) => {
    const result = await showOpenDialog(event, {
      title: "选择新项目的保存位置",
      defaultPath: await rememberedDialogPaths().defaultPath("new-project-parent"),
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || result.filePaths.length === 0) {
      return failure("cancelled", "已取消保存位置选择");
    }
    await rememberDialogPath("new-project-parent", result.filePaths[0], "directory");
    const name = normalizedProjectDirectoryName(request?.suggestedName);
    const target = path.join(result.filePaths[0], name);
    try {
      await access(target);
      return failure("failed", "目标项目目录已经存在，请调整项目名称或保存位置");
    } catch (error) {
      if (error?.code !== "ENOENT") {
        return failure("failed", readableError(error));
      }
    }
    return success(target);
  });

  ipcMain.handle(channels.selectExternalRuntimePath, async (event, request) => {
    const pathKind = request?.pathKind;
    if (pathKind !== "file" && pathKind !== "directory") {
      return failure("failed", "外部运行环境路径类型无效");
    }
    const displayName = normalizedRuntimeDisplayName(request?.displayName);
    const memoryKey = `external-runtime:${String(request?.runtimeId ?? "unknown")}:${pathKind}`;
    const options = {
      title: `选择 ${displayName}`,
      defaultPath: await rememberedDialogPaths().defaultPath(memoryKey),
      properties: [pathKind === "file" ? "openFile" : "openDirectory"],
    };
    if (pathKind === "file" && process.platform === "win32") {
      options.filters = [{ name: "可执行文件", extensions: ["exe"] }];
    }
    const result = await showOpenDialog(event, options);
    if (result.canceled || result.filePaths.length === 0) {
      return failure("cancelled", "已取消路径选择");
    }
    await rememberDialogPath(memoryKey, result.filePaths[0], pathKind);
    return success(result.filePaths[0]);
  });

  ipcMain.handle(channels.selectExportTarget, async (event, request) => {
    const suggestedName = normalizedSuggestedName(request?.suggestedName);
    const assetPackage =
      request?.mediaType === "application/vnd.verdflow.asset-catalog+zip";
    const profilePackage =
      request?.mediaType === "application/vnd.verdflow.role-binding-profile+zip";
    const strategyPackage =
      request?.mediaType === "application/vnd.verdflow.strategy-package+zip";
    const authoringKit =
      request?.mediaType === "application/vnd.verdflow.authoring-kit+zip";
    const title = authoringKit
      ? "导出 VerdFlow Agent 创作工具包"
      : strategyPackage
        ? "导出 VerdFlow 策略包"
        : profilePackage
          ? "导出 VerdFlow 植被搭配包"
          : assetPackage
            ? "导出 VerdFlow 资产包"
            : "保存 VerdFlow 输出";
    const filter = authoringKit
      ? { name: "VerdFlow Agent 创作工具包", extensions: ["zip"] }
      : strategyPackage
        ? { name: "VerdFlow 策略包", extensions: ["vfs"] }
        : profilePackage
          ? { name: "VerdFlow 植被搭配包", extensions: ["vfp"] }
          : assetPackage
            ? { name: "VerdFlow 资产包", extensions: ["vfa"] }
            : { name: "DTS XML", extensions: ["xml"] };
    const memoryKey = `export:${String(request?.mediaType ?? "default")}`;
    const result = await showSaveDialog(event, {
      title,
      defaultPath: await rememberedDialogPaths().defaultPath(memoryKey, suggestedName),
      filters: [filter],
    });
    if (result.canceled || !result.filePath) {
      return failure("cancelled", "已取消保存");
    }
    await rememberDialogPath(memoryKey, result.filePath, "file");
    return success(result.filePath);
  });

  ipcMain.handle(channels.saveExport, async (event, request) => {
    let temporaryPath = null;
    try {
      const sessionId = normalizedResourceId(request?.sessionId, "Session ID");
      const exportId = normalizedResourceId(request?.exportId, "Export ID");
      const suggestedName = normalizedSuggestedName(request?.suggestedName);
      const result = await showSaveDialog(event, {
        title: "保存 VerdFlow 输出",
        defaultPath: await rememberedDialogPaths().defaultPath("export:dts-xml", suggestedName),
        filters: [{ name: "DTS XML", extensions: ["xml"] }],
      });
      if (result.canceled || !result.filePath) {
        return failure("cancelled", "已取消保存");
      }
      await rememberDialogPath("export:dts-xml", result.filePath, "file");

      const target = new URL(
        `/api/v1/projects/${encodeURIComponent(sessionId)}/exports/${encodeURIComponent(exportId)}/content`,
        localApi.baseUrl,
      );
      const headers = { Accept: "application/xml, text/xml;q=0.9, */*;q=0.1" };
      if (localApi.token !== null) {
        headers.Authorization = `Bearer ${localApi.token}`;
      }
      temporaryPath = path.join(
        path.dirname(result.filePath),
        `.${path.basename(result.filePath)}.${process.pid}.${Date.now()}.tmp`,
      );
      await downloadLocalApiFile({
        target,
        headers,
        destination: temporaryPath,
      });
      await rm(result.filePath, { force: true });
      await rename(temporaryPath, result.filePath);
      temporaryPath = null;
      return success(result.filePath);
    } catch (error) {
      return failure("failed", readableError(error));
    } finally {
      if (temporaryPath) await rm(temporaryPath, { force: true }).catch(() => {});
    }
  });

  ipcMain.handle(channels.launchDtsExplorer, async (_event, request) => {
    try {
      const launch = normalizedDtsExplorerLaunch(request);
      if (
        dtsExplorerLaunch?.sessionId === launch.sessionId &&
        dtsExplorerLaunch.process.exitCode === null
      ) {
        return success({
          processId: dtsExplorerLaunch.process.pid,
          reused: true,
        });
      }
      await access(launch.executablePath);
      const child = spawn(
        launch.executablePath,
        [`-webui=${launch.launchUrl}`],
        {
          cwd: path.dirname(launch.executablePath),
          stdio: "ignore",
          windowsHide: false,
        },
      );
      await new Promise((resolve, reject) => {
        child.once("spawn", resolve);
        child.once("error", reject);
      });
      dtsExplorerLaunch = { sessionId: launch.sessionId, process: child };
      child.once("exit", () => {
        if (dtsExplorerLaunch?.process === child) dtsExplorerLaunch = null;
      });
      child.unref();
      return success({ processId: child.pid, reused: false });
    } catch (error) {
      return failure("failed", readableError(error));
    }
  });

  ipcMain.handle(channels.revealPath, async (_event, rawPath) => {
    try {
      const resolved = normalizedAbsolutePath(rawPath);
      const info = await stat(resolved);
      if (info.isDirectory()) {
        const message = await shell.openPath(resolved);
        return message ? failure("failed", message) : success(undefined);
      }
      shell.showItemInFolder(resolved);
      return success(undefined);
    } catch (error) {
      return failure("failed", readableError(error));
    }
  });
};

const createWindow = async () => {
  diagnostic("creating browser window");
  Menu.setApplicationMenu(null);
  await access(windowIconPath);
  const window = new BrowserWindow({
    title: "VerdFlow Studio",
    icon: windowIconPath,
    width: 1440,
    height: 900,
    minWidth: 920,
    minHeight: 640,
    autoHideMenuBar: true,
    show: false,
    backgroundColor: "#f4f7f6",
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  window.webContents.setWindowOpenHandler(manualWindowOpenHandler({
    openExternal: (url) => shell.openExternal(url),
    onError: () => {
      if (!window.isDestroyed()) {
        dialog.showErrorBox("无法打开用户手册", `请在浏览器中访问：\n${USER_MANUAL_URL}`);
      }
    },
  }));
  window.on("page-title-updated", (event) => event.preventDefault());
  window.webContents.on("will-navigate", (event, target) => {
    if (target !== window.webContents.getURL()) {
      event.preventDefault();
    }
  });
  window.once("ready-to-show", () => window.show());

  const developmentUrl = process.env.VERDFLOW_STUDIO_URL?.trim();
  if (developmentUrl) {
    const url = new URL(developmentUrl);
    if (
      url.protocol !== "http:" ||
      !["127.0.0.1", "localhost", "::1"].includes(url.hostname)
    ) {
      throw new Error("VERDFLOW_STUDIO_URL must be a loopback HTTP address");
    }
    await window.loadURL(url.href);
  } else {
    await access(rendererDist);
    await window.loadFile(rendererDist);
  }
  diagnostic("renderer loaded");
  return window;
};

const showOpenDialog = (event, options) => {
  const owner = BrowserWindow.fromWebContents(event.sender);
  return owner
    ? dialog.showOpenDialog(owner, options)
    : dialog.showOpenDialog(options);
};

const showSaveDialog = (event, options) => {
  const owner = BrowserWindow.fromWebContents(event.sender);
  return owner
    ? dialog.showSaveDialog(owner, options)
    : dialog.showSaveDialog(options);
};

const readableError = (error) =>
  error instanceof Error ? error.message : "桌面能力执行失败";

const normalizedRuntimeDisplayName = (value) => {
  if (typeof value !== "string") return "外部运行环境";
  const trimmed = value.trim();
  return trimmed && trimmed.length <= 80 ? trimmed : "外部运行环境";
};

const hasInstanceLock = app.requestSingleInstanceLock();
if (!hasInstanceLock) {
  app.quit();
} else {
  registerHandlers();
  diagnostic("IPC handlers registered; waiting for Electron ready");
  app.whenReady()
    .then(async () => {
      diagnostic("Electron ready");
      await startPackagedLocalApi();
      const window = await createWindow();
      await updater.initialize();
      if (process.env.VERDFLOW_PORTABLE_SMOKE_TEST === "1") {
        window.hide();
        setTimeout(() => app.quit(), 750);
      }
    })
    .catch(async (error) => {
      process.stderr.write(
        `[verdflow-desktop] startup failed: ${readableError(error)}\n`,
      );
      await stopPackagedLocalApi();
      app.exit(1);
    });

  app.on("second-instance", () => {
    const [window] = BrowserWindow.getAllWindows();
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.focus();
  });

  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      await createWindow();
    }
  });

  app.on("before-quit", (event) => {
    updater.dispose();
    if (
      portable === null ||
      packagedShutdownStarted ||
      localApiProcess === null ||
      localApiProcess.exitCode !== null ||
      localApiProcess.signalCode !== null
    ) {
      return;
    }
    event.preventDefault();
    packagedShutdownStarted = true;
    void stopPackagedLocalApi().finally(() => app.quit());
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
      app.quit();
    }
  });
}
