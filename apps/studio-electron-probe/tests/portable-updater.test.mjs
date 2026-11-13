import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, readdir, realpath, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { PortableUpdater } from "../src/portable-updater.mjs";

const repository = "example/verdflow-test";
const archiveName = "VerdFlow-Studio-v0.4.0-win-x64.zip";
async function fixture({ source = repository, corrupt = false, prepareFailure = false, latestStatus = 200, metadataStatus = 200, latestFailures = 0, interruptedDownloads = 0, shortDownloads = 0, retryDelays = [0, 0], latestHeaders = {}, usePathAlias = false } = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdflow-update-test-"));
  const install = path.join(root, "application"), data = path.join(root, "data");
  await mkdir(path.join(install, "resources/app"), { recursive: true });
  await mkdir(data);
  await writeFile(path.join(install, "original"), "keep-old-app");
  await writeFile(path.join(data, "project"), "keep-project-data");
  await writeFile(path.join(install, "resources/app/updates.json"), JSON.stringify({ schema: "verdflow.update-source", version: "1", repository: source }));
  const bytes = Buffer.from("an isolated archive fixture");
  let fetches = 0, launches = 0, apiCalls = 0, archiveCalls = 0, preparations = 0;
  const updater = new PortableUpdater({
    portable: { rootPath: install, resourcesPath: path.join(install, "resources"), userDataPath: usePathAlias ? path.join(root, "DATA") : data, pythonExecutablePath: "unused" }, version: "0.3.1", scriptRoot: "unused", retryDelays,
    fetcher: async url => {
      fetches++;
      if (String(url).includes("api.github.com")) {
        apiCalls++;
        if (apiCalls <= latestFailures) throw new TypeError("fetch failed", { cause: { code: "ECONNRESET" } });
        if (latestStatus !== 200) return new Response(null, { status: latestStatus, headers: latestHeaders });
      }
      if (String(url).endsWith(".json") && metadataStatus !== 200) return new Response(null, { status: metadataStatus });
      if (String(url).includes("api.github.com")) return Response.json({ tag_name: "v0.4.0", assets: [
        { name: "VerdFlow-update-win-x64.json", browser_download_url: `https://github.com/${repository}/releases/download/v0.4.0/VerdFlow-update-win-x64.json` },
        { name: archiveName, size: bytes.length, browser_download_url: `https://github.com/${repository}/releases/download/v0.4.0/${archiveName}` },
      ] });
      if (String(url).endsWith(".json")) return Response.json({ schema: "verdflow.portable-update", version: "1", product_version: "0.4.0", platform: "windows-x64", archive_name: archiveName, archive_bytes: bytes.length, archive_sha256: corrupt ? "0".repeat(64) : createHash("sha256").update(bytes).digest("hex") });
      archiveCalls++;
      if (archiveCalls <= interruptedDownloads) {
        let sent = false;
        return new Response(new ReadableStream({ pull(controller) {
          if (!sent) { sent = true; controller.enqueue(bytes.subarray(0, 5)); }
          else controller.error(Object.assign(new Error("fixture body interrupted"), { code: "ECONNRESET" }));
        } }));
      }
      return new Response(archiveCalls <= shortDownloads ? bytes.subarray(0, 5) : bytes);
    },
    prepare: async ({ planPath, signal }) => {
      preparations++;
      signal.throwIfAborted();
      const plan = JSON.parse(await readFile(planPath, "utf8"));
      await mkdir(plan.stageRoot);
      await writeFile(path.join(plan.stageRoot, ".verdflow-update-ready.json"), JSON.stringify({ id: plan.id, version: plan.targetVersion }));
      await writeFile(path.join(plan.stageRoot, "VerdFlow Studio.exe"), "new-program");
      if (prepareFailure) throw new Error("fixture failed");
    },
    launch: async () => { launches++; },
  });
  await updater.initialize();
  return { root, data, install, updater, bytes, fetches: () => fetches, launches: () => launches, apiCalls: () => apiCalls, archiveCalls: () => archiveCalls, preparations: () => preparations,
    cleanup: async () => { updater.dispose(); if (path.dirname(root) === os.tmpdir() && path.basename(root).startsWith("verdflow-update-test-")) await rm(root, { recursive: true }); } };
}

test("transient check and interrupted streams recover automatically with a fresh archive", async () => {
  const f = await fixture({ latestFailures: 1, interruptedDownloads: 1 });
  try {
    await f.updater.check();
    assert.equal(f.updater.snapshot().stage, "ready");
    assert.equal(f.apiCalls(), 2);
    assert.equal(f.archiveCalls(), 2);
    assert.equal(f.preparations(), 1);
    assert.deepEqual(await readFile(f.updater.plan.archive), f.bytes);
    const logs = JSON.parse(await readFile(path.join(f.data, "Updates/diagnostics.json"), "utf8"));
    assert.deepEqual(logs.map(item => item.phase), ["release", "downloading"]);
    assert.ok(logs.every(item => item.category === "network" && item.retry === 1));
    assert.equal(f.launches(), 0);
  } finally { await f.cleanup(); }
});

test("incomplete downloads retry, but checksum and preparation failures do not", async () => {
  for (const options of [{ shortDownloads: 1 }, { corrupt: true }, { prepareFailure: true }]) {
    const f = await fixture(options);
    try {
      await f.updater.check();
      assert.equal(f.archiveCalls(), options.shortDownloads ? 2 : 1);
      assert.equal(f.updater.snapshot().stage, options.shortDownloads ? "ready" : "available");
      const logs = JSON.parse(await readFile(path.join(f.data, "Updates/diagnostics.json"), "utf8"));
      if (options.corrupt) assert.equal(logs.at(-1).category, "checksum");
      if (options.prepareFailure) assert.equal(logs.at(-1).phase, "preparing");
      assert.equal(f.launches(), 0);
    } finally { await f.cleanup(); }
  }
});

test("retry budget is bounded, diagnostics omit raw secrets, and a manual retry remains possible", async () => {
  const f = await fixture({ latestFailures: 3 });
  try {
    await f.updater.check();
    assert.equal(f.apiCalls(), 3);
    assert.equal(f.updater.snapshot().stage, "error");
    assert.match(f.updater.snapshot().message, /连接中断或超时/);
    await f.updater.check();
    assert.equal(f.updater.snapshot().stage, "ready");
    const error = Object.assign(new Error("secret-query-and-private-path"), { code: "EACCES" });
    for (let i = 0; i < 45; i++) await f.updater.diagnostic("downloading", error);
    const content = await readFile(path.join(f.data, "Updates/diagnostics.json"), "utf8");
    assert.equal(JSON.parse(content).length, 40);
    assert.ok(!content.includes("secret-query-and-private-path") && !content.includes(f.root));
  } finally { await f.cleanup(); }
});

test("rate limits are not hammered and cancelling backoff stops the next transfer", async () => {
  const limited = await fixture({ latestStatus: 403, latestHeaders: { "x-ratelimit-remaining": "0" } });
  try {
    await limited.updater.check();
    assert.equal(limited.apiCalls(), 1);
    assert.match(limited.updater.snapshot().message, /访问次数/);
  } finally { await limited.cleanup(); }
  const f = await fixture({ interruptedDownloads: 5, retryDelays: [5000, 5000], usePathAlias: true });
  try {
    f.updater.notify = state => { if (state.message?.includes("自动重试")) f.updater.cancel(); };
    await f.updater.check();
    assert.equal(f.archiveCalls(), 1);
    assert.equal(f.updater.snapshot().stage, "available");
    assert.match(f.updater.snapshot().message, /已取消/);
    assert.equal(f.preparations(), 0);
    assert.deepEqual((await readdir(path.join(f.data, "Updates"))).filter(name => /^[a-f0-9]{32}$/.test(name)), []);
  } finally { await f.cleanup(); }
});

test("broken or unwritable diagnostic storage never blocks a valid update", async () => {
  for (const blocked of [false, true]) {
    const f = await fixture({ latestFailures: 1 });
    try {
      const file = path.join(f.data, "Updates/diagnostics.json");
      if (blocked) await mkdir(file);
      else await writeFile(file, "incomplete json");
      await f.updater.check();
      assert.equal(f.updater.snapshot().stage, "ready");
    } finally { await f.cleanup(); }
  }
});

test("unconfigured source makes no network requests", async () => {
  const f = await fixture({ source: null });
  try {
    await f.updater.check(); await f.updater.download();
    assert.equal(f.fetches(), 0);
    assert.equal(f.updater.snapshot().configured, false);
    await f.updater.preferences({ autoCheck: false, autoDownload: false });
    assert.deepEqual(JSON.parse(await readFile(path.join(f.data, "Updates/preferences.json"))), { autoCheck: false, autoDownload: false });
  } finally { await f.cleanup(); }
});

test("a configured repository without a public release is a normal idle state", async () => {
  const f = await fixture({ latestStatus: 404 });
  try {
    await f.updater.check();
    const state = f.updater.snapshot();
    assert.equal(state.configured, true);
    assert.equal(state.stage, "idle");
    assert.equal(state.latestVersion, null);
    assert.ok(state.lastCheckedAt);
    assert.match(state.message, /暂未发现公开发布版本/);
    assert.equal(f.fetches(), 1);
    assert.equal(f.launches(), 0);
  } finally { await f.cleanup(); }
});

for (const options of [{ latestStatus: 503 }, { metadataStatus: 404 }]) test(`release errors remain retryable: ${JSON.stringify(options)}`, async () => {
  const f = await fixture(options);
  try {
    await f.updater.check();
    assert.equal(f.updater.snapshot().stage, "error");
    assert.equal(f.launches(), 0);
  } finally { await f.cleanup(); }
});

test("check/download stages a verified update without modifying current app or data", async () => {
  const f = await fixture();
  try {
    await f.updater.check();
    assert.equal(f.updater.snapshot().stage, "ready");
    assert.equal(await readFile(path.join(f.install, "original"), "utf8"), "keep-old-app");
    assert.equal(await readFile(path.join(f.data, "project"), "utf8"), "keep-project-data");
    const restored = new PortableUpdater({ portable: f.updater.portable, version: "0.3.1", scriptRoot: "unused" });
    try { await restored.initialize(); assert.equal(restored.snapshot().stage, "ready"); } finally { restored.dispose(); }
    await f.updater.apply();
    assert.equal(f.launches(), 1);
    assert.equal(f.updater.snapshot().stage, "applying");
  } finally { await f.cleanup(); }
});

test("startup cleans only successful backups for this installation whose files match the manifest", async () => {
  const f = await fixture();
  try {
    const installRoot = await realpath(f.install);
    const cache = path.join(f.data, "Updates");
    const program = "original program";
    const backups = new Map();
    for (const [index, kind] of [
      "applied", "older-applied", "foreign", "failed", "future", "no-manifest",
      "extra", "edited", "unconfirmed", "linked", "no-record",
    ].entries()) {
      const id = index.toString(16).repeat(32);
      const backup = path.join(f.root, `.verdflow-backup-${id}`);
      backups.set(kind, backup);
      if (kind === "linked") await symlink(f.install, backup, "junction");
      else {
        await mkdir(backup);
        await writeFile(path.join(backup, "VerdFlow Studio.exe"), kind === "edited" ? "modified program" : program);
        if (kind !== "no-manifest") await writeFile(path.join(backup, "update-manifest.json"), JSON.stringify({
          schema: "verdflow.portable-files", version: "1", product_version: "0.3.0", platform: "windows-x64",
          files: [{ path: "VerdFlow Studio.exe", bytes: program.length, sha256: createHash("sha256").update(program).digest("hex") }],
        }));
        if (kind === "extra") await writeFile(path.join(backup, "my-project"), "keep");
      }
      if (kind === "no-record") continue;
      const job = path.join(cache, id);
      const targetVersion = kind === "future" ? "0.5.0" : kind === "older-applied" ? "0.3.1" : "0.4.0";
      await mkdir(job);
      await writeFile(path.join(job, "plan.json"), JSON.stringify({
        id, repository, installRoot: kind === "foreign" ? path.join(f.root, "another-install") : installRoot,
        targetVersion, parentPid: 12345,
      }));
      await writeFile(path.join(job, "result.json"), JSON.stringify({ status: kind === "failed" ? "failed" : "applied", version: targetVersion }));
      if (kind !== "unconfirmed") await writeFile(path.join(job, "helper-accepted.json"), JSON.stringify({ id, parentPid: 12345 }));
    }
    await f.updater.cleanupAppliedBackups();
    assert.equal(await readFile(path.join(backups.get("applied"), "VerdFlow Studio.exe"), "utf8"), program);
    const restarted = new PortableUpdater({ portable: f.updater.portable, version: "0.4.0", scriptRoot: "unused" });
    try {
      await restarted.initialize();
      await restarted.backupCleanup;
      const remaining = new Set(await readdir(f.root));
      for (const [kind, backup] of backups) {
        assert.equal(remaining.has(path.basename(backup)), !["applied", "older-applied"].includes(kind), kind);
      }
      assert.equal(await readFile(path.join(backups.get("extra"), "my-project"), "utf8"), "keep");
      assert.equal(await readFile(path.join(backups.get("edited"), "VerdFlow Studio.exe"), "utf8"), "modified program");
      assert.equal(await readFile(path.join(f.install, "original"), "utf8"), "keep-old-app");
      assert.equal(await readFile(path.join(f.data, "project"), "utf8"), "keep-project-data");
    } finally { restarted.dispose(); }
  } finally { await f.cleanup(); }
});

for (const scenario of [{ corrupt: true }, { prepareFailure: true }]) test(`failed update leaves app/data intact: ${JSON.stringify(scenario)}`, async () => {
  const f = await fixture(scenario);
  try {
    await f.updater.check();
    assert.equal(f.updater.snapshot().stage, "available");
    assert.equal(f.updater.plan, null);
    assert.equal(await readFile(path.join(f.install, "original"), "utf8"), "keep-old-app");
    assert.equal(await readFile(path.join(f.data, "project"), "utf8"), "keep-project-data");
    await assert.rejects(f.updater.apply());
    assert.equal(f.launches(), 0);
  } finally { await f.cleanup(); }
});

test("automatic download can be disabled; cancellation clears owned staging files", async () => {
  const f = await fixture();
  try {
    await f.updater.preferences({ autoCheck: false, autoDownload: false });
    await f.updater.check();
    assert.equal(f.updater.snapshot().stage, "available");
    f.updater.prepare = async ({ signal }) => { f.updater.cancel(); signal.throwIfAborted(); };
    await f.updater.download();
    assert.equal(f.updater.snapshot().stage, "available");
    assert.match(f.updater.snapshot().message, /取消/);
  } finally { await f.cleanup(); }
});

test("missing prepared files do not trap the user in a non-retryable ready state", async () => {
  const f = await fixture();
  try {
    await f.updater.check();
    await rm(path.join(f.updater.plan.stageRoot, "VerdFlow Studio.exe"));
    await assert.rejects(f.updater.apply(), /重新检查更新/);
    assert.equal(f.updater.snapshot().stage, "error");
    assert.equal(f.launches(), 0);
    await f.updater.check();
    assert.equal(f.updater.snapshot().stage, "ready");
  } finally { await f.cleanup(); }
});
