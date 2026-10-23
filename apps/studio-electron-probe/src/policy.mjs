import path from "node:path";

const MAX_API_PATH_LENGTH = 4096;
const MAX_API_BODY_LENGTH = 4 * 1024 * 1024;

export const localApiConfig = (environment) => {
  const raw = environment.VERDFLOW_LOCAL_API_BASE_URL || "http://127.0.0.1:8768";
  const url = new URL(raw);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "::1"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("VerdFlow Local API must use a loopback HTTP origin");
  }
  const token = environment.VERDFLOW_LOCAL_API_MANAGEMENT_TOKEN?.trim() || null;
  return Object.freeze({ baseUrl: url.origin, token });
};

export const normalizedApiRequest = (request, baseUrl) => {
  if (!request || typeof request !== "object") {
    throw new TypeError("Local API request must be an object");
  }
  const method = request.method || "GET";
  if (!["GET", "POST", "PUT", "DELETE"].includes(method)) {
    throw new TypeError("Local API method is not allowed");
  }
  if (
    typeof request.path !== "string" ||
    request.path.length > MAX_API_PATH_LENGTH ||
    !request.path.startsWith("/api/v1/") ||
    request.path.includes("\\") ||
    /[\r\n]/.test(request.path)
  ) {
    throw new TypeError("Local API path is invalid");
  }
  const target = new URL(request.path, baseUrl);
  if (target.origin !== baseUrl || !target.pathname.startsWith("/api/v1/")) {
    throw new TypeError("Local API path is outside the allowed scope");
  }
  if (
    request.body !== undefined &&
    (typeof request.body !== "string" || request.body.length > MAX_API_BODY_LENGTH)
  ) {
    throw new TypeError("Local API body is invalid");
  }
  return { method, target: target.href, body: request.body };
};

export const normalizedExtensions = (extensions) => {
  const values = Array.isArray(extensions) ? extensions : [];
  const normalized = values
    .filter((value) => typeof value === "string")
    .map((value) => value.toLowerCase().replace(/^\./, ""))
    .filter((value) => /^[a-z0-9][a-z0-9-]{0,31}$/.test(value));
  return normalized.length > 0 ? [...new Set(normalized)] : ["fbx"];
};

export const normalizedSuggestedName = (value) => {
  if (typeof value !== "string" || !value.trim()) {
    return "verdflow-output.xml";
  }
  const name = path.basename(value.trim());
  return /\.(?:xml|vfa|vfp|vfs|zip)$/i.test(name) ? name : `${name}.xml`;
};

export const normalizedResourceId = (value, label) => {
  if (
    typeof value !== "string" ||
    !/^[A-Za-z0-9._-]{1,200}$/.test(value)
  ) {
    throw new TypeError(`${label} is invalid`);
  }
  return value;
};

export const normalizedProjectDirectoryName = (value) => {
  const raw = typeof value === "string" ? value.trim() : "";
  const normalized = raw
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/[. ]+$/g, "")
    .slice(0, 96);
  const name = normalized || "VerdFlow Project";
  return /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(name)
    ? `${name}-project`
    : name;
};

export const normalizedAbsolutePath = (value) => {
  if (typeof value !== "string" || !path.isAbsolute(value)) {
    throw new TypeError("Path must be absolute");
  }
  return path.resolve(value);
};

export const normalizedDtsExplorerLaunch = (request) => {
  if (!request || typeof request !== "object") {
    throw new TypeError("DTS Explorer launch request must be an object");
  }
  const executablePath = normalizedAbsolutePath(request.executablePath);
  if (path.basename(executablePath).toLowerCase() !== "dts explorer.exe") {
    throw new TypeError("DTS Explorer executable is invalid");
  }
  const launchUrl = new URL(request.launchUrl);
  if (
    launchUrl.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "::1"].includes(launchUrl.hostname) ||
    launchUrl.username ||
    launchUrl.password ||
    !launchUrl.hash.includes("session=") ||
    !launchUrl.hash.includes("bootstrap=")
  ) {
    throw new TypeError("DTS Explorer WebUI URL is invalid");
  }
  const sessionId = normalizedResourceId(
    request.sessionId,
    "DTS Live Session ID",
  );
  return {
    executablePath,
    launchUrl: launchUrl.href,
    sessionId,
  };
};
