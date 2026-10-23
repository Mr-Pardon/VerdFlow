import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { PortableUpdater } from "../src/portable-updater.mjs";

const repository = "example/verdflow-test";
const archiveName = "VerdFlow-Studio-v0.4.0-win-x64.zip";
async function fixture({ source = repository, corrupt = false, prepareFailure = false, latestStatus = 200, metadataStatus = 200 } = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdflow-update-test-"));
  const install = path.join(root, "application"), data = path.join(root, "data");
  await mkdir(path.join(install, "resources/app"), { recursive: true });
  await mkdir(data);
  await writeFile(path.join(install, "original"), "keep-old-app");
  await writeFile(path.join(data, "project"), "keep-project-data");
  await writeFile(path.join(install, "resources/app/updates.json"), JSON.stringify({ schema: "verdflow.update-source", version: "1", repository: source }));
  const bytes = Buffer.from("an isolated archive fixture");
  let fetches = 0, launches = 0;
  const updater = new PortableUpdater({
    portable: { rootPath: install, resourcesPath: path.join(install, "resources"), userDataPath: data, pythonExecutablePath: "unused" }, version: "0.3.1", scriptRoot: "unused",
    fetcher: async url => {
      fetches++;
      if (String(url).includes("api.github.com") && latestStatus !== 200) return new Response(null, { status: latestStatus });
      if (String(url).endsWith(".json") && metadataStatus !== 200) return new Response(null, { status: metadataStatus });
      if (String(url).includes("api.github.com")) return Response.json({ tag_name: "v0.4.0", assets: [
        { name: "VerdFlow-update-win-x64.json", browser_download_url: `https://github.com/${repository}/releases/download/v0.4.0/VerdFlow-update-win-x64.json` },
        { name: archiveName, size: bytes.length, browser_download_url: `https://github.com/${repository}/releases/download/v0.4.0/${archiveName}` },
      ] });
      if (String(url).endsWith(".json")) return Response.json({ schema: "verdflow.portable-update", version: "1", product_version: "0.4.0", platform: "windows-x64", archive_name: archiveName, archive_bytes: bytes.length, archive_sha256: corrupt ? "0".repeat(64) : createHash("sha256").update(bytes).digest("hex") });
      return new Response(bytes);
    },
    prepare: async ({ planPath, signal }) => {
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
  return { root, data, install, updater, fetches: () => fetches, launches: () => launches,
    cleanup: async () => { updater.dispose(); if (path.dirname(root) === os.tmpdir() && path.basename(root).startsWith("verdflow-update-test-")) await rm(root, { recursive: true }); } };
}

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
