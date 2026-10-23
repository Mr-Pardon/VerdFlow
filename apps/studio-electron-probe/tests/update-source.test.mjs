import assert from "node:assert/strict";
import test from "node:test";
import { assertUpdateIdle, newerVersion, releaseAssets, releaseRepository, updateDescriptor, updateJson, updateResponse } from "../src/update-source.mjs";

export const repository = "example/verdflow-test";
export const archiveName = "VerdFlow-Studio-v0.4.0-win-x64.zip";
export const releaseDocument = (bytes = 12) => ({ tag_name: "v0.4.0", draft: false, prerelease: false, assets: [
  { name: "VerdFlow-update-win-x64.json", browser_download_url: `https://github.com/${repository}/releases/download/v0.4.0/VerdFlow-update-win-x64.json` },
  { name: archiveName, size: bytes, browser_download_url: `https://github.com/${repository}/releases/download/v0.4.0/${archiveName}` },
] });
export const descriptorDocument = (bytes = 12, sha256 = "a".repeat(64)) => ({ schema: "verdflow.portable-update", version: "1", product_version: "0.4.0", platform: "windows-x64", archive_name: archiveName, archive_bytes: bytes, archive_sha256: sha256 });

test("stable versions compare numerically and never downgrade", () => {
  assert.equal(newerVersion("0.10.0", "0.9.9"), true);
  assert.equal(newerVersion("0.3.1", "0.3.1"), false);
  assert.equal(newerVersion("0.3.0", "0.3.1"), false);
  for (const version of ["0.4.0-beta", "01.2.3", "v0.4.0", "../../next"]) assert.throws(() => newerVersion(version, "0.3.1"));
  assert.equal(releaseRepository(null), null);
  assert.throws(() => releaseRepository("https://github.com/example/repo"));
});

test("release identity pins archive and metadata to one repository and version", () => {
  const release = releaseAssets(releaseDocument(), repository, "0.3.1");
  assert.equal(updateDescriptor(descriptorDocument(), release).version, "0.4.0");
  assert.equal(releaseAssets(releaseDocument(), repository, "0.5.0"), null);
  assert.throws(() => releaseAssets({ ...releaseDocument(), prerelease: true }, repository, "0.3.1"));
  const altered = releaseDocument(); altered.assets[1].browser_download_url = "https://github.com/other/repo/release.zip";
  assert.throws(() => releaseAssets(altered, repository, "0.3.1"));
  for (const change of [{ archive_bytes: 11 }, { product_version: "0.5.0" }, { archive_sha256: "unknown" }, { platform: "linux" }]) {
    assert.throws(() => updateDescriptor({ ...descriptorDocument(), ...change }, release));
  }
});

test("downloads forbid insecure or external redirects and oversized metadata", async () => {
  let calls = 0;
  await assert.rejects(updateResponse("http://github.com/test", { fetcher: async () => { calls++; } }));
  await assert.rejects(updateResponse("https://github.com/test", { fetcher: async () => { calls++; return new Response(null, { status: 302, headers: { location: "http://127.0.0.1/private" } }); } }));
  assert.equal(calls, 1);
  await assert.rejects(updateJson("https://api.github.com/test", { fetcher: async (_url, options) => {
    assert.equal(options.headers.Accept, "application/vnd.github+json");
    assert.equal(options.headers.Authorization, undefined);
    return new Response("x".repeat(1024 ** 2 + 1));
  } }));
});

test("restart requires stopped live service and no active project workflow or task", async () => {
  const read = (live = "stopped", business = "completed", tasks = []) => async url => {
    if (url.endsWith("dts-live/status")) return { state: live };
    if (url.endsWith("/projects")) return { items: [{ state: "active", session_id: "test-session" }] };
    if (url.endsWith("/summary")) return { state: business };
    return { items: tasks };
  };
  await assertUpdateIdle(read());
  await assert.rejects(assertUpdateIdle(read("connected")), /停止实时/);
  await assert.rejects(assertUpdateIdle(read("stopped", "running")), /项目流程/);
  await assert.rejects(assertUpdateIdle(read("stopped", "completed", [{ status: "running" }])), /任务/);
  await assert.rejects(assertUpdateIdle(async url => url.endsWith("status") ? { state: "stopped" } : { items: [{ state: "closing" }] }), /打开或关闭/);
});
