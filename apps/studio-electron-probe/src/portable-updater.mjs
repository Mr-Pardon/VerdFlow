import { createWriteStream } from "node:fs";
import { access, lstat, mkdir, readFile, realpath, rmdir, rm, writeFile } from "node:fs/promises";
import { createHash, randomBytes } from "node:crypto";
import path from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { newerVersion, releaseAssets, releaseRepository, UpdateHttpError, updateDescriptor, updateJson, updateResponse } from "./update-source.mjs";
import { launchPortableUpdate, preparePortableUpdate } from "./update-runner.mjs";

export class PortableUpdater {
  constructor({ portable, version, scriptRoot, notify = () => {}, fetcher = fetch, prepare = preparePortableUpdate, launch = launchPortableUpdate }) {
    Object.assign(this, { portable, version, scriptRoot, notify, fetcher, prepare, launch });
    this.repository = null;
    this.release = null;
    this.plan = null;
    this.timer = null;
    this.abort = null;
    this.closed = false;
    this.state = { supported: process.platform === "win32" && portable !== null, configured: false, currentVersion: version,
      stage: "idle", latestVersion: null, progress: 0, message: null, autoCheck: true, autoDownload: true, lastCheckedAt: null };
  }

  snapshot() { return { ...this.state }; }
  update(values) { Object.assign(this.state, values); if (!this.closed) this.notify(this.snapshot()); }

  async initialize() {
    if (!this.state.supported) return;
    this.cache = path.join(this.portable.userDataPath, "Updates");
    try {
      await mkdir(this.cache, { recursive: true });
      const source = JSON.parse(await readFile(path.join(this.portable.resourcesPath, "app/updates.json"), "utf8"));
      if (source.schema !== "verdflow.update-source" || source.version !== "1") throw new Error("更新源配置无效");
      this.repository = releaseRepository(source.repository);
      try {
        const preferences = JSON.parse(await readFile(path.join(this.cache, "preferences.json"), "utf8"));
        for (const key of ["autoCheck", "autoDownload"]) if (typeof preferences[key] === "boolean") this.state[key] = preferences[key];
      } catch (error) { if (error.code !== "ENOENT") this.state.message = "更新偏好读取失败，使用默认设置"; }
      this.update({ configured: this.repository !== null });
      if (this.repository) await this.restorePrepared();
      this.schedule(3000);
    } catch { this.update({ message: "当前程序包未配置有效的更新源" }); }
  }

  schedule(delay = 6 * 60 * 60 * 1000) {
    clearTimeout(this.timer);
    if (this.closed || !this.repository || !this.state.autoCheck) return;
    this.timer = setTimeout(() => { void this.check().catch(() => this.update({ stage: "error", message: "自动更新暂不可用，现有程序未改变" })).finally(() => this.schedule()); }, delay);
    this.timer.unref?.();
  }

  async preferences(input) {
    if (!this.state.supported || !input || typeof input.autoCheck !== "boolean" || typeof input.autoDownload !== "boolean") throw new Error("更新选项无效");
    await writeFile(path.join(this.cache, "preferences.json"), JSON.stringify({ autoCheck: input.autoCheck, autoDownload: input.autoDownload }), "utf8");
    this.update({ autoCheck: input.autoCheck, autoDownload: input.autoDownload });
    this.schedule();
    return this.snapshot();
  }

  async check() {
    if (!this.repository || ["checking", "downloading", "preparing", "applying", "ready"].includes(this.state.stage) || this.closed) return this.snapshot();
    this.abort = new AbortController();
    const signal = AbortSignal.any([this.abort.signal, AbortSignal.timeout(30_000)]);
    this.update({ stage: "checking", message: null });
    let readingLatestRelease = true;
    try {
      const document = await updateJson(`https://api.github.com/repos/${this.repository}/releases/latest`, { fetcher: this.fetcher, signal });
      readingLatestRelease = false;
      const release = releaseAssets(document, this.repository, this.version);
      this.release = release ? updateDescriptor(await updateJson(release.metadataUrl, { fetcher: this.fetcher, signal }), release) : null;
      this.update({ stage: this.release ? "available" : "idle", latestVersion: this.release?.version ?? null, lastCheckedAt: new Date().toISOString() });
    } catch (error) {
      if (readingLatestRelease && error instanceof UpdateHttpError && error.status === 404) {
        this.release = null;
        this.update({ stage: "idle", latestVersion: null, lastCheckedAt: new Date().toISOString(), message: "暂未发现公开发布版本" });
      } else {
        this.update({ stage: "error", message: "检查更新失败，请稍后重试；不影响继续使用" });
      }
    }
    finally { this.abort = null; }
    if (this.release && this.state.stage === "available" && this.state.autoDownload && !this.closed) return this.download();
    return this.snapshot();
  }

  async download() {
    if (!this.release || ["checking", "downloading", "preparing", "applying", "ready"].includes(this.state.stage) || this.closed) return this.snapshot();
    const release = this.release;
    const id = randomBytes(16).toString("hex");
    const installRoot = await realpath(this.portable.rootPath);
    const dataRoot = await realpath(this.portable.userDataPath);
    if (dataRoot === installRoot || dataRoot.startsWith(installRoot + path.sep)) throw new Error("用户数据位于程序目录内，请先分离数据目录再更新");
    const directory = path.join(this.cache, id);
    const stageRoot = path.join(path.dirname(installRoot), `.verdflow-update-${id}`);
    let ownsDirectory = false, ownsStage = false;
    this.abort = new AbortController();
    const signal = AbortSignal.any([this.abort.signal, AbortSignal.timeout(60 * 60 * 1000)]);
    this.update({ stage: "downloading", progress: 0, message: null });
    try {
      await mkdir(directory);
      ownsDirectory = true;
      // Test sibling write permission before transferring a potentially large archive.
      await mkdir(stageRoot);
      ownsStage = true;
      await rmdir(stageRoot);
      const archive = path.join(directory, release.archiveName);
      const response = await updateResponse(release.archiveUrl, { fetcher: this.fetcher, signal });
      const digest = createHash("sha256");
      let received = 0, emitted = 0;
      const monitor = new Transform({ transform: (chunk, _encoding, callback) => {
        received += chunk.length;
        if (received > release.bytes) return callback(new Error("更新包大小超出发布记录"));
        digest.update(chunk);
        if (Date.now() - emitted > 250) { emitted = Date.now(); this.update({ progress: Math.min(99, Math.floor(received / release.bytes * 100)) }); }
        callback(null, chunk);
      } });
      await pipeline(Readable.fromWeb(response.body), monitor, createWriteStream(archive, { flags: "wx" }), { signal });
      if (received !== release.bytes || digest.digest("hex") !== release.sha256) throw new Error("更新包校验失败，请重新下载");
      const plan = { id, repository: this.repository, installRoot, stageRoot, archive, sha256: release.sha256,
        targetVersion: release.version, rootName: release.rootName, parentPid: process.pid };
      const planPath = path.join(directory, "plan.json");
      await writeFile(planPath, JSON.stringify(plan), "utf8");
      this.update({ stage: "preparing", progress: 100 });
      await this.prepare({ python: this.portable.pythonExecutablePath, script: path.join(this.scriptRoot, "prepare-update.py"), planPath, signal });
      signal.throwIfAborted();
      this.plan = { ...plan, planPath };
      await writeFile(path.join(this.cache, "pending.json"), JSON.stringify({ id }), "utf8");
      this.update({ stage: "ready", progress: 100 });
    } catch (error) {
      this.update({ stage: "available", progress: 0, message: signal.aborted ? "更新下载已取消或超时，可重新下载" : (error.code === "EACCES" || error.code === "EPERM") ? "程序目录不可写，请移动到有写入权限的目录后再更新" : "下载或校验更新失败，现有程序未改变，请重试" });
      if (ownsStage) await removeOwnedDirectory(stageRoot, path.join(path.dirname(installRoot), `.verdflow-update-${id}`));
      if (ownsDirectory) await removeOwnedDirectory(directory, path.join(this.cache, id));
    } finally { this.abort = null; }
    return this.snapshot();
  }

  async restorePrepared() {
    try {
      const { id } = JSON.parse(await readFile(path.join(this.cache, "pending.json"), "utf8"));
      if (typeof id !== "string" || !/^[a-f0-9]{32}$/.test(id)) return;
      const planPath = path.join(this.cache, id, "plan.json");
      const plan = JSON.parse(await readFile(planPath, "utf8"));
      if (plan.id !== id || plan.repository !== this.repository || !newerVersion(plan.targetVersion, this.version) || plan.installRoot !== await realpath(this.portable.rootPath) || plan.stageRoot !== path.join(path.dirname(plan.installRoot), `.verdflow-update-${id}`)) return;
      const rootName = `VerdFlow-Studio-v${plan.targetVersion}-win-x64`;
      if (plan.rootName !== rootName || plan.archive !== path.join(this.cache, id, rootName + ".zip") || !/^[a-f0-9]{64}$/.test(plan.sha256)) return;
      const marker = JSON.parse(await readFile(path.join(plan.stageRoot, ".verdflow-update-ready.json"), "utf8"));
      if (marker.id !== id || marker.version !== plan.targetVersion) return;
      this.plan = { ...plan, planPath };
      this.update({ stage: "ready", latestVersion: plan.targetVersion, progress: 100 });
    } catch { /* A stale or incomplete cache cannot become an installable update. */ }
  }

  cancel() { if (["downloading", "preparing"].includes(this.state.stage)) this.abort?.abort(); return this.snapshot(); }

  async apply() {
    if (!this.plan || this.state.stage !== "ready") throw new Error("更新包尚未准备好");
    try {
      if (this.plan.installRoot !== await realpath(this.portable.rootPath) || this.plan.stageRoot !== path.join(path.dirname(this.plan.installRoot), `.verdflow-update-${this.plan.id}`)) throw new Error("Invalid update location");
      const marker = JSON.parse(await readFile(path.join(this.plan.stageRoot, ".verdflow-update-ready.json"), "utf8"));
      if (marker.id !== this.plan.id || marker.version !== this.plan.targetVersion) throw new Error("Invalid update marker");
      await access(path.join(this.plan.stageRoot, "VerdFlow Studio.exe"));
    } catch {
      this.plan = null;
      this.release = null;
      await rm(path.join(this.cache, "pending.json"), { force: true }).catch(() => undefined);
      const message = "更新暂存文件已改变，请重新检查更新";
      this.update({ stage: "error", latestVersion: null, progress: 0, message });
      throw new Error(message);
    }
    await writeFile(this.plan.planPath, JSON.stringify({ ...this.plan, parentPid: process.pid }), "utf8");
    this.update({ stage: "applying", message: null });
    try { await this.launch({ script: path.join(this.scriptRoot, "apply-update.ps1"), planPath: this.plan.planPath }); }
    catch (error) { this.update({ stage: "ready", message: "无法启动更新器，程序未退出，请重试" }); throw error; }
  }

  dispose() { this.closed = true; clearTimeout(this.timer); this.abort?.abort(); }
}

async function removeOwnedDirectory(target, expected) {
  if (path.resolve(target) !== path.resolve(expected)) return;
  try {
    const info = await lstat(target);
    if (info.isDirectory() && !info.isSymbolicLink() && await realpath(target) === target) await rm(target, { recursive: true });
  } catch { /* Failed cleanup must never widen its target or affect the installed app. */ }
}
