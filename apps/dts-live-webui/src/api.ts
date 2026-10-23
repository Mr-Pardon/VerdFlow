import type {
  CatalogAssetPage,
  CatalogSummary,
  ConfigurationOptions,
  Correlation,
  LiveCommand,
  LiveRegion,
  ProfileDetail,
  SaveProfileRequest,
  SelectionConfiguration,
  SessionSnapshot,
} from "./types";
import type { StrategyPreviewClient } from "@verdflow/strategy-preview-ui";

interface ErrorEnvelope {
  error?: { code?: string };
}

export class ConnectorApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
  }
}

const apiRequest = async <T>(
  path: string,
  init: RequestInit = {},
): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  const document = (await response.json().catch(() => ({}))) as ErrorEnvelope;
  if (!response.ok) {
    const code = document.error?.code ?? `http_${response.status}`;
    throw new ConnectorApiError(readableCode(code, path), response.status, code);
  }
  return document as T;
};

export const bootstrapSession = async () => {
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ""));
  const sessionId = fragment.get("session");
  const nonce = fragment.get("bootstrap");
  if (sessionId && nonce) {
    await apiRequest<{ connector_session_id: string; expires_at: string }>(
      "/api/v1/bootstrap/exchange",
      {
        method: "POST",
        body: JSON.stringify({
          connector_session_id: sessionId,
          bootstrap_nonce: nonce,
        }),
      },
    );
    history.replaceState(null, "", `${location.pathname}${location.search}`);
  }
  return apiRequest<SessionSnapshot>("/api/v1/session");
};

export const configurationOptions = () =>
  apiRequest<ConfigurationOptions>("/api/v1/configuration/options");

export const strategyPreviewClient: StrategyPreviewClient = {
  submitStrategyExample: (input) =>
    apiRequest("/api/v1/strategy-previews", {
      method: "POST",
      body: JSON.stringify(input),
      keepalive: true,
    }),
  strategyExample: (id) =>
    apiRequest(`/api/v1/strategy-previews/${encodeURIComponent(id)}`),
  cancelStrategyExample: (id) =>
    apiRequest(`/api/v1/strategy-previews/${encodeURIComponent(id)}`, {
      method: "DELETE",
      keepalive: true,
    }),
  previewAssetAssignments: (input) =>
    apiRequest("/api/v1/asset-assignment-previews", {
      method: "POST",
      body: JSON.stringify(input),
    }),
};

export const closeStrategyExamples = () =>
  apiRequest("/api/v1/strategy-previews/cancel-all", {
    method: "POST",
    body: "{}",
    keepalive: true,
  });

export const catalogAssets = (
  catalog: Pick<CatalogSummary, "catalog_id">,
) =>
  apiRequest<CatalogAssetPage>(
    "/api/v1/configuration/assets",
    {
      method: "POST",
      body: JSON.stringify(catalog),
    },
  );

export const searchAssets = (input: import("@verdflow/asset-browser-ui").AssetSearchRequest, signal: AbortSignal) =>
  apiRequest<import("@verdflow/asset-browser-ui").AssetSearchPage<import("./types").AssetInfo>>(
    "/api/v1/configuration/asset-search", { method: "POST", body: JSON.stringify(input), signal },
  );

export const profileDetail = (profileId: string) =>
  apiRequest<ProfileDetail>(
    `/api/v1/configuration/profiles/${encodeURIComponent(profileId)}`,
  );

export const assetThumbnailUrl = (resourceId: string) =>
  `/api/v1/configuration/resources/${encodeURIComponent(resourceId)}`;

export const saveProfile = (request: SaveProfileRequest) =>
  apiRequest<ProfileDetail>("/api/v1/configuration/profiles", {
    method: "POST",
    body: JSON.stringify(request),
  });

export const beginSelection = (configuration: SelectionConfiguration & { regions: LiveRegion[] }) =>
  apiRequest<{ correlation: Correlation; regions: LiveRegion[] }>("/api/v1/operations/selection", {
    method: "POST",
    body: JSON.stringify(configuration),
  });

export const exportRegionsShapefile = (regions: LiveRegion[]) =>
  apiRequest<{ saved: boolean; path: string | null; region_count: number }>(
    "/api/v1/selections/export-shp",
    { method: "POST", body: JSON.stringify({ regions }) },
  );

export const importRegionsShapefile = () =>
  apiRequest<{ imported: boolean; source_name: string | null; regions: LiveRegion[] }>(
    "/api/v1/selections/import-shp", { method: "POST", body: "{}" },
  );

export const regenerateSelection = (configuration: SelectionConfiguration) =>
  apiRequest("/api/v1/operations/regeneration", {
    method: "POST",
    body: JSON.stringify(configuration),
  });

export const sessionSnapshot = () =>
  apiRequest<SessionSnapshot>("/api/v1/session?include_regions=false");

export const commandsAfter = (sequence: number) =>
  apiRequest<{ items: LiveCommand[] }>(
    `/api/v1/commands?after=${sequence}&timeout_ms=20000`,
  );

export const submitSelection = (
  correlation: Correlation,
  coordinates: number[][],
) =>
  apiRequest("/api/v1/callbacks/selection", {
    method: "POST",
    body: JSON.stringify({ correlation, coordinates }),
  });

export const submitElevation = (
  correlation: Correlation,
  intersects: unknown,
  failed_regions: { region_id: string; reason: string }[] = [],
  chunked = false,
) =>
  apiRequest("/api/v1/callbacks/elevation", {
    method: "POST",
    body: JSON.stringify({ correlation, intersects, failed_regions, chunked }),
  });

export const submitElevationChunk = (correlation: Correlation, indices: number[], intersects: unknown) =>
  apiRequest("/api/v1/callbacks/elevation-chunk", {
    method: "POST", body: JSON.stringify({ correlation, indices, intersects }),
  });

export const submitDelivery = (
  correlation: Correlation,
  exportId: string,
  succeeded: boolean,
  message?: string,
) =>
  apiRequest("/api/v1/callbacks/delivery", {
    method: "POST",
    body: JSON.stringify({
      correlation,
      export_id: exportId,
      succeeded,
      message,
    }),
  });

export const cancelOperation = () =>
  apiRequest<SessionSnapshot>("/api/v1/operations/cancel", {
    method: "POST",
    body: "{}",
  });

const readableCode = (code: string, path: string) => {
  if (code === "invalid_request") {
    if (path === "/api/v1/callbacks/elevation" || path === "/api/v1/callbacks/elevation-chunk") {
      return "高程采样结果回传失败，服务端未接受本次采样数据";
    }
    if (path === "/api/v1/callbacks/delivery") return "XML 导入结果回传失败";
    if (path === "/api/v1/operations/selection" || path === "/api/v1/operations/regeneration") {
      return "种植请求无效，请检查策略、植被映射及选区数据";
    }
    return "请求参数无效，请检查当前操作的数据";
  }
  return ({
    authorization_failed: "会话授权已失效，请从 VerdFlow Studio 重新打开面板",
    session_not_found: "当前快速种植会话已结束",
    operation_busy: "当前区域仍在处理中，请完成或取消后再试",
    payload_too_large: "本次点位数据超过实时传输上限，请增大植被间距或缩小区域",
    configuration_unavailable: "本地策略与资产配置暂不可用",
    preview_not_allowed: "当前策略尚未信任或已撤销信任，示例暂不可用",
    resource_not_found: "实时种植服务版本已更新，请在 Studio 中停止后重新启动",
    sdk_not_configured: "请先在 VerdFlow Studio 设置 DTS Explorer 根目录",
    internal_error: "本地服务未能完成请求",
    export_busy: "选区文件窗口已经打开，请先完成或取消当前操作",
    export_unavailable: "选区导出暂不可用，请重启实时种植服务",
    export_save_failed: "选区保存失败，请检查目标目录权限或文件是否被占用",
    import_unavailable: "选区导入暂不可用，请重启实时种植服务",
    shp_read_failed: "无法读取 SHP 文件，请检查文件是否存在且可访问",
    shp_polygon_required: "请选择仅包含面（Polygon）的 SHP 数据",
    shp_holes_unsupported: "该文件含带洞面，当前实时选区暂不支持；未导入任何面",
    shp_invalid_geometry: "该文件包含无效或空的面，请先修复后导入",
    shp_invalid_dataset: "无法解析 SHP，请检查文件完整性及 .cpg 字符编码",
    shp_single_dataset_required: "ZIP 中应仅包含一套 SHP 数据",
    shp_companions_missing: "SHP 缺少配套的 .shx 或 .dbf，请放在同一目录后重试",
    shp_empty: "文件中没有可导入的面",
    shp_too_large: "选区数据过大，请拆分文件或简化面轮廓后再导入",
  })[code] ?? `Connector 请求失败：${code}`;
};
