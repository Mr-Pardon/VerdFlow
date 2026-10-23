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
  constructor(status) {
    super(`更新服务暂不可用（HTTP ${status}）`);
    this.status = status;
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
    if (!response.ok) { await response.body?.cancel(); throw new UpdateHttpError(response.status); }
    return response;
  }
  throw new Error("更新下载重定向次数过多");
}

export async function updateJson(url, options) {
  const response = await updateResponse(url, options);
  const chunks = [];
  let length = 0;
  for await (const chunk of response.body) {
    length += chunk.length;
    if (length > 1024 ** 2) throw new Error("更新信息过大");
    chunks.push(Buffer.from(chunk));
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
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
