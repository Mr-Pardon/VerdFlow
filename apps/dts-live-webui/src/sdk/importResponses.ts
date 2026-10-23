import { asRecord } from "./runtime";

export interface ImportResult {
  succeeded: boolean;
  message: string;
  path: string | null;
}

export const readImportResult = (value: unknown): ImportResult | null => {
  let record = asRecord(value);
  if (typeof value === "string") {
    try { record = asRecord(JSON.parse(value)); } catch { /* Native Windows paths may be unescaped. */ }
  }
  if (record?.eventtype === "ImportXMLObject") {
    const result = typeof record.resultMessage === "string" ? record.resultMessage : "";
    const succeeded = /^(succeed|succeeded|success)$/i.test(result);
    return {
      succeeded,
      path: typeof record.path === "string" ? record.path : null,
      message: succeeded
        ? result || "Succeed"
        : String(record.message || result || "DTS XML 导入失败"),
    };
  }
  if (typeof value !== "string" || !/"eventtype"\s*:\s*"ImportXMLObject"/.test(value)) {
    return null;
  }
  const result = readLooseStringField(value, "resultMessage");
  const succeeded = /^(succeed|succeeded|success)$/i.test(result ?? "");
  return {
    succeeded,
    path: readLooseStringField(value, "path")?.replaceAll("\\\\", "\\") ?? null,
    message: succeeded
      ? result || "Succeed"
      : readLooseStringField(value, "message") || result || "DTS XML 导入失败",
  };
};

export const normalizeImportPath = (path: string) => {
  const slashes = path.trim().replaceAll("\\", "/");
  const prefix = slashes.startsWith("//") ? "//" : "";
  return (prefix + slashes.slice(prefix.length).replace(/\/{2,}/g, "/")).toLowerCase();
};

// Only use this on the return value of the current SDK invocation. Broadcast
// events have no such ownership and still require an exact XML path match.
export const readDirectImportResult = (value: unknown): ImportResult | null => {
  const event = readImportResult(value);
  if (event) return event;
  if (typeof value === "string") {
    try { return readDirectImportResult(JSON.parse(value)); } catch { /* Plain status text is also supported. */ }
    if (/^(succeed|succeeded|success|ok)$/i.test(value.trim())) {
      return { succeeded: true, message: value, path: null };
    }
    return null;
  }
  const record = asRecord(value);
  if (!record || typeof record.result !== "number") return null;
  return {
    succeeded: record.result === 0,
    message: String(record.resultMessage || record.message || (record.result === 0 ? "Succeed" : "DTS XML 导入失败")),
    path: typeof record.path === "string" ? record.path : null,
  };
};

export const readLooseStringField = (document: string, field: string) => {
  const match = document.match(new RegExp(`"${field}"\\s*:\\s*"([^"\\r\\n]*)"`, "i"));
  return match?.[1] ?? null;
};
