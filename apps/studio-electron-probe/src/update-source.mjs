import { setTimeout as pause } from "node:timers/promises";

export const UPDATE_ASSET = "VerdFlow-update-win-x64.json";
export const MAX_ARCHIVE_BYTES = 1024 ** 3;

export const releaseRepository = (value) => {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value)) {
    throw new Error("更新仓库配置无效");
  }
  return value;
};

const versionParts = (value) => {
  if (typeof value !== "string" || !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value)) throw new Error("更新版本号无效");
  return value.split(".").map(BigInt);
};

export const newerVersion = (candidate, current) => {
  const next = versionParts(candidate), previous = versionParts(current);
  for (let i = 0; i < next.length; i++) {
    if (next[i] !== previous[i]) return next[i] > previous[i];
  }
  return false;
};

const assetUrl = (value, repository, tag, name) => {
  const expected = `https://github.com/${repository}/releases/download/${encodeURIComponent(tag)}/${encodeURIComponent(name)}`;
  if (value !== expected) throw new Error("更新下载地址不属于指定的发布仓库");
  return expected;
};

export const releaseAssets = (document, repository, currentVersion) => {
  if (!document || document.draft || document.prerelease || typeof document.tag_name !== "string") throw new Error("更新不是正式发布版本");
  const version = document.tag_name.replace(/^v/, "");
  if (!newerVersion(version, currentVersion)) return null;
  if (!Array.isArray(document.assets)) throw new Error("发布附件列表无效");
  const metadata = document.assets.filter(asset => asset.name === UPDATE_ASSET);
  const archiveName = `VerdFlow-Studio-v${version}-win-x64.zip`;
  const archives = document.assets.filter(asset => asset.name === archiveName);
  if (metadata.length !== 1 || archives.length !== 1) throw new Error("该版本尚未提供完整的免安装更新包");
  const archive = archives[0];
  if (!Number.isSafeInteger(archive.size) || archive.size <= 0 || archive.size > MAX_ARCHIVE_BYTES) throw new Error("更新包大小无效");
  return {
    version, archiveName, bytes: archive.size,
    metadataUrl: assetUrl(metadata[0].browser_download_url, repository, document.tag_name, UPDATE_ASSET),
    archiveUrl: assetUrl(archive.browser_download_url, repository, document.tag_name, archiveName),
  };
};

export const updateDescriptor = (document, release) => {
  if (
    !document || document.schema !== "verdflow.portable-update" || document.version !== "1" ||
    document.platform !== "windows-x64" || document.product_version !== release.version ||
    document.archive_name !== release.archiveName || document.archive_bytes !== release.bytes ||
    typeof document.archive_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(document.archive_sha256)
  ) throw new Error("更新包身份或校验信息不匹配");
  return { ...release, sha256: document.archive_sha256, rootName: release.archiveName.slice(0, -4) };
};

const downloadHosts = new Set(["api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"]);

export class UpdateHttpError extends Error {
  constructor(status, headers = new Headers()) {
    super(`更新服务暂不可用（HTTP ${status}）`);
    this.status = status;
    this.rateLimited = status === 429 || (status === 403 && headers.get("x-ratelimit-remaining") === "0");
    const raw = headers.get("retry-after");
    const seconds = raw && !Number.isFinite(Number(raw)) ? (Date.parse(raw) - Date.now()) / 1000 : Number(raw);
    this.retryAfterSeconds = Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 86400) : 0;
  }
}

const transientCodes = new Set(["ECONNRESET", "ECONNREFUSED", "ECONNABORTED", "ETIMEDOUT", "EHOSTUNREACH", "ENETUNREACH", "EAI_AGAIN", "EPIPE", "UND_ERR_SOCKET", "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_HEADERS_TIMEOUT", "UND_ERR_BODY_TIMEOUT", "UPDATE_INCOMPLETE"]);
const errorCode = error => [error?.code, error?.cause?.code, ...(Array.isArray(error?.cause?.errors) ? error.cause.errors.map(item => item?.code) : [])]
  .find(value => typeof value === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(value));

export function updateFailureInfo(error) {
  const code = errorCode(error);
  const category = error instanceof UpdateHttpError ? error.rateLimited ? "rate-limit" : "http"
    : ["TimeoutError", "AbortError"].includes(error?.name) ? "timeout-or-cancel"
    : transientCodes.has(code) || (error?.name === "TypeError" && error?.message === "fetch failed" && !code) ? "network"
    : ["EACCES", "EPERM"].includes(code) ? "permission"
    : code === "ENOSPC" ? "disk-space"
    : code === "UPDATE_CHECKSUM" ? "checksum" : "validation-or-preparation";
  return { category, ...(code ? { code } : {}), ...(error instanceof UpdateHttpError ? { status: error.status } : {}) };
}

export function updateFailureMessage(phase, error) {
  const { category, status } = updateFailureInfo(error);
  if (category === "rate-limit") return "GitHub 访问次数已达限制，请稍后再检查更新";
  if (category === "permission") return "程序或更新目录不可写，请确认目录权限后重试";
  if (category === "disk-space") return "磁盘可用空间不足，请清理空间后重试";
  if (category === "checksum") return "更新包完整性校验失败，未替换程序，请重新下载";
  if (category === "network" || category === "timeout-or-cancel") return "更新连接中断或超时，请检查网络或代理后重试";
  if (category === "http") return `更新服务暂不可用（HTTP ${status}），请稍后重试`;
  if (phase === "preparing") return "更新包解压或逐文件校验失败，未替换程序，请重试";
  if (phase === "downloading") return "下载或校验更新失败，现有程序未改变，请重试";
  return "更新信息读取或校验失败，请稍后重试；不影响继续使用";
}

export async function retryUpdate(operation, { signal, onRetry, delays = [700, 1600] } = {}) {
  const bounded = delays.slice(0, 2);
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted();
    try { return await operation(attempt); }
    catch (error) {
      const retryable = error instanceof UpdateHttpError
        ? !error.rateLimited && error.retryAfterSeconds <= 3 && [408, 425, 500, 502, 503, 504].includes(error.status)
        : updateFailureInfo(error).category === "network";
      if (signal?.aborted || !retryable || attempt >= bounded.length) throw error;
      const delay = Math.max(bounded[attempt], (error.retryAfterSeconds ?? 0) * 1000);
      await onRetry?.(error, attempt + 1);
      await pause(delay, undefined, { signal });
    }
  }
}

export async function updateResponse(url, { signal, fetcher = fetch } = {}) {
  let target = new URL(url);
  for (let count = 0; count < 6; count++) {
    if (target.protocol !== "https:" || target.username || target.password || !downloadHosts.has(target.hostname) || target.port) {
      throw new Error("更新连接不属于允许的 HTTPS 发布源");
    }
    const response = await fetcher(target, {
      signal, redirect: "manual", headers: { Accept: target.hostname === "api.github.com" ? "application/vnd.github+json" : "application/octet-stream", "User-Agent": "VerdFlow-Updater" },
    });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get("location");
      await response.body?.cancel();
      if (!location) throw new Error("更新下载重定向无效");
      target = new URL(location, target);
      continue;
    }
    if (!response.ok) { await response.body?.cancel(); throw new UpdateHttpError(response.status, response.headers); }
    return response;
  }
  throw new Error("更新下载重定向次数过多");
}

export async function updateJson(url, options = {}) {
  return retryUpdate(async () => {
    const response = await updateResponse(url, options);
    const chunks = [];
    let length = 0;
    for await (const chunk of response.body) {
      length += chunk.length;
      if (length > 1024 ** 2) throw new Error("更新信息过大");
      chunks.push(Buffer.from(chunk));
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  }, { signal: options.signal, onRetry: options.onRetry, delays: options.retryDelays });
}

export async function assertUpdateIdle(read) {
  const live = await read("/api/v1/dts-live/status");
  if (live.state !== "stopped") throw new Error("请先在 DTS 工作台停止实时种植服务，再重启更新");
  const projects = await read("/api/v1/projects");
  if (!Array.isArray(projects.items)) throw new Error("无法确认当前任务状态");
  for (const project of projects.items) {
    if (["closed", "failed"].includes(project.state)) continue;
    if (project.state !== "active") throw new Error("项目正在打开或关闭，请稍后更新");
    const summary = await read(`/api/v1/projects/${encodeURIComponent(project.session_id)}/summary`);
    if (typeof summary.state !== "string" || summary.state === "running") throw new Error("仍有项目流程正在执行，请完成或取消后再更新");
    const tasks = await read(`/api/v1/projects/${encodeURIComponent(project.session_id)}/tasks?status=queued,running,cancelling&limit=1`);
    if (!Array.isArray(tasks.items) || tasks.items.length) throw new Error("仍有任务正在执行，请完成或取消后再更新");
  }
}
