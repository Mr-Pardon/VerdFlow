import type { DtsApi, DtsWindow, UnknownRecord } from "./protocol";
import { isPromise, readableError } from "./runtime";
import { normalizeImportPath, readDirectImportResult, readImportResult } from "./importResponses";

interface PendingImport {
  path: string;
  resolve: () => void;
  reject: (error: Error) => void;
  warningTimer: number;
}

export class XmlImportCancelled extends Error {}

export class DtsXmlImport {
  private pending: PendingImport | null = null;
  private restoreWrites: (() => void) | null = null;
  private readonly receivers = new WeakSet<Function>();
  private readonly restoreReceivers: (() => void)[] = [];
  private buffer = "";

  constructor(private readonly onActivity: (message: string) => void) {}

  install(api: DtsApi) {
    this.guardDocumentWrites();
    if (!api.isCalledInCEF) return;
    const target = window as DtsWindow;
    this.wrapReceiver(api, "onConnectionMessage");
    if (target.__g) this.wrapReceiver(target.__g, "onConnectionMessage");
    this.wrapReceiver(target as unknown as UnknownRecord, "__onCefResponse");
    if (target.acapi) this.wrapReceiver(target.acapi as unknown as UnknownRecord, "__onCefResponse");
  }

  execute(api: DtsApi, xmlPath: string): Promise<void> {
    if (this.pending) throw new Error("已有 DTS XML 正在导入");
    return new Promise((resolve, reject) => {
      const pending: PendingImport = {
        path: normalizeImportPath(xmlPath), resolve, reject,
        warningTimer: window.setTimeout(() => {
          if (this.pending === pending) this.onActivity("DTS 导入耗时较长，仍在等待完成回执");
        }, 30_000),
      };
      this.pending = pending;
      this.buffer = "";
      this.onActivity("正在加载到 DTS");
      try {
        const result = api.call?.({
          __command: "ImportXMLObject", command: "ImportXMLObject",
          data: [{ type: 0, path: xmlPath.replaceAll("\\", "/") }],
        });
        // A request-scoped business result is also a receipt, even without a
        // path. Unrelated acknowledgements and late promises cannot settle it.
        if (isPromise(result)) {
          void result.then(response => {
            this.consumeDirect(response, pending);
          }, error => {
            if (this.pending === pending) this.finish(new Error(readableError(error)));
          });
        } else this.consumeDirect(result, pending);
      } catch (error) { this.finish(new Error(readableError(error))); }
    });
  }

  consume(raw: unknown): boolean {
    const result = readImportResult(raw);
    if (!result) return false;
    // Consume stale/foreign callbacks so the SDK cannot document.write the SPA.
    if (this.pending && result.path && normalizeImportPath(result.path) === this.pending.path) {
      this.finish(result.succeeded ? undefined : new Error(result.message));
    }
    return true;
  }

  cancel() {
    if (!this.pending) return null;
    const message = "已停止等待 XML 回执；DTS 原生导入可能仍在执行，请检查场景";
    this.finish(new XmlImportCancelled(message));
    return message;
  }

  dispose() {
    this.cancel();
    this.restoreWrites?.();
    this.restoreWrites = null;
    for (const restore of this.restoreReceivers.splice(0)) restore();
    this.buffer = "";
  }

  private finish(error?: Error) {
    const pending = this.pending;
    if (!pending) return;
    this.pending = null;
    window.clearTimeout(pending.warningTimer);
    if (error) pending.reject(error);
    else pending.resolve();
  }

  private consumeDirect(raw: unknown, pending: PendingImport) {
    if (this.pending !== pending) return;
    const result = readDirectImportResult(raw);
    if (!result || (result.path && normalizeImportPath(result.path) !== pending.path)) return;
    this.finish(result.succeeded ? undefined : new Error(result.message));
  }

  private guardDocumentWrites() {
    if (this.restoreWrites) return;
    const originalWrite = document.write;
    const originalWriteln = document.writeln;
    const intercept = (...parts: string[]) => {
      const fragment = parts.join("");
      this.buffer = `${this.buffer}${fragment}\n`.slice(-65_536);
      if (this.consume(fragment) || this.consume(this.buffer)) {
        this.buffer = "";
        return;
      }
      console.warn("DTS SDK document output was suppressed:", fragment);
    };
    document.write = intercept;
    document.writeln = intercept;
    this.restoreWrites = () => {
      if (document.write === intercept) document.write = originalWrite;
      if (document.writeln === intercept) document.writeln = originalWriteln;
    };
  }

  private wrapReceiver(target: UnknownRecord, field: string) {
    const original = target[field];
    if (typeof original !== "function" || this.receivers.has(original)) return;
    const bridge = this;
    const wrapped = function (this: unknown, raw: unknown, source?: unknown) {
      if (!bridge.consume(raw)) return original.call(this, raw, source);
    };
    this.receivers.add(wrapped);
    target[field] = wrapped;
    this.restoreReceivers.push(() => { if (target[field] === wrapped) target[field] = original; });
  }
}
