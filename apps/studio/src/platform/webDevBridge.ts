import type {
  BridgeResult,
  DtsExplorerLaunchRequest,
  DtsExplorerLaunchResult,
  LocalApiConnection,
  LocalApiRequest,
  LocalApiResponse,
  PlatformBridge,
  SaveFileRequest,
  SaveExportRequest,
} from "./bridge";
import { unsupported } from "./bridge";

const normalizedBaseUrl = (value: string | undefined): string => {
  const trimmed = value?.trim();
  if (!trimmed || trimmed === "/") {
    return "";
  }
  return trimmed.replace(/\/$/, "");
};

export const createWebDevBridge = (): PlatformBridge => {
  const connection: LocalApiConnection = {
    baseUrl: normalizedBaseUrl(import.meta.env.VITE_VERDFLOW_API_BASE_URL),
    managementAvailable: false,
  };

  return {
    kind: "web-dev",
    capabilities: {
      selectSourceFile: false,
      selectSourceFiles: false,
      resolveDroppedFilePath: false,
      selectStrategyPackageDirectory: false,
      selectProjectDirectory: false,
      selectNewProjectDirectory: false,
      selectExportTarget: false,
      selectExternalRuntimePath: false,
      saveExport: false,
      revealPath: false,
      launchDtsExplorer: false,
    },
    async getLocalApiConnection(): Promise<BridgeResult<LocalApiConnection>> {
      return { ok: true, value: connection };
    },
    async requestLocalApi(
      request: LocalApiRequest,
    ): Promise<BridgeResult<LocalApiResponse>> {
      try {
        const headers = new Headers({ Accept: "application/json" });
        if (request.body !== undefined) {
          headers.set("Content-Type", "application/json");
        }
        const response = await fetch(`${connection.baseUrl}${request.path}`, {
          method: request.method || "GET",
          body: request.body,
          headers,
        });
        return {
          ok: true,
          value: {
            status: response.status,
            contentType: response.headers.get("Content-Type") || "",
            body: await response.text(),
          },
        };
      } catch (error) {
        return {
          ok: false,
          reason: "failed",
          message: error instanceof Error ? error.message : "无法连接本地服务",
        };
      }
    },
    async selectSourceFile(): Promise<BridgeResult<string>> {
      return unsupported("本地 Source 文件选择");
    },
    async selectSourceFiles(): Promise<BridgeResult<string[]>> {
      return unsupported("本地 Source 文件批量选择");
    },
    async resolveDroppedFilePath(): Promise<BridgeResult<string>> {
      return unsupported("拖入本地文件");
    },
    async selectStrategyPackageDirectory(): Promise<BridgeResult<string>> {
      return unsupported("策略包目录选择");
    },
    async selectProjectDirectory(): Promise<BridgeResult<string>> {
      return unsupported("项目目录选择");
    },
    async selectNewProjectDirectory(): Promise<BridgeResult<string>> {
      return unsupported("新项目保存位置选择");
    },
    async selectExternalRuntimePath(): Promise<BridgeResult<string>> {
      return unsupported("外部运行环境路径选择");
    },
    async selectExportTarget(
      _request: SaveFileRequest,
    ): Promise<BridgeResult<string>> {
      return unsupported("导出位置选择");
    },
    async saveExport(_request: SaveExportRequest): Promise<BridgeResult<string>> {
      return unsupported("桌面文件保存");
    },
    async launchDtsExplorer(
      _request: DtsExplorerLaunchRequest,
    ): Promise<BridgeResult<DtsExplorerLaunchResult>> {
      return unsupported("DTS Explorer 启动");
    },
    async revealPath(_path: string): Promise<BridgeResult<void>> {
      return unsupported("在资源管理器中显示");
    },
  };
};
