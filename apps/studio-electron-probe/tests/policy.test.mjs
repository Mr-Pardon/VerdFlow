import assert from "node:assert/strict";
import { readFile, rm } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  localApiConfig,
  normalizedApiRequest,
  normalizedDtsExplorerLaunch,
  normalizedExtensions,
  normalizedProjectDirectoryName,
  normalizedResourceId,
  normalizedSuggestedName,
} from "../src/policy.mjs";
import { downloadLocalApiFile } from "../src/local-api-download.mjs";

test("local API configuration only accepts a loopback HTTP origin", () => {
  assert.equal(localApiConfig({}).baseUrl, "http://127.0.0.1:8768");
  assert.throws(
    () => localApiConfig({ VERDFLOW_LOCAL_API_BASE_URL: "https://127.0.0.1:8768" }),
    /loopback HTTP origin/,
  );
  assert.throws(
    () => localApiConfig({ VERDFLOW_LOCAL_API_BASE_URL: "http://example.com" }),
    /loopback HTTP origin/,
  );
});

test("export save resource identifiers cannot escape the controlled route", () => {
  assert.equal(normalizedResourceId("run_01-a", "Execution ID"), "run_01-a");
  assert.throws(
    () => normalizedResourceId("../exports/secret", "Export ID"),
    /Export ID is invalid/,
  );
});

test("local API request remains inside the versioned endpoint scope", () => {
  const request = normalizedApiRequest(
    { path: "/api/v1/projects?limit=10" },
    "http://127.0.0.1:8768",
  );
  assert.equal(request.target, "http://127.0.0.1:8768/api/v1/projects?limit=10");
  assert.throws(
    () => normalizedApiRequest(
      { path: "/api/v1/%2e%2e/health" },
      "http://127.0.0.1:8768",
    ),
    /outside the allowed scope/,
  );
  assert.throws(
    () => normalizedApiRequest(
      { path: "/api/v1/projects", body: "x".repeat(4 * 1024 * 1024 + 1) },
      "http://127.0.0.1:8768",
    ),
    /body is invalid/,
  );
});

test("dialog inputs are normalized without leaking arbitrary filters", () => {
  assert.deepEqual(normalizedExtensions([".FBX", "fbx", "../../exe"]), ["fbx"]);
  assert.deepEqual(normalizedExtensions(["vfa"]), ["vfa"]);
  assert.deepEqual(normalizedExtensions(["vfp"]), ["vfp"]);
  assert.deepEqual(normalizedExtensions(["vfs"]), ["vfs"]);
  assert.equal(normalizedSuggestedName("../planting"), "planting.xml");
  assert.equal(
    normalizedSuggestedName("../dts-default-r1.vfa"),
    "dts-default-r1.vfa",
  );
  assert.equal(
    normalizedSuggestedName("../grove-profile.vfp"),
    "grove-profile.vfp",
  );
  assert.equal(
    normalizedSuggestedName("../natural-grove.vfs"),
    "natural-grove.vfs",
  );
  assert.equal(
    normalizedSuggestedName("../vfs-author.zip"),
    "vfs-author.zip",
  );
  assert.equal(normalizedProjectDirectoryName("ABU:C5. "), "ABU-C5");
  assert.equal(normalizedProjectDirectoryName("CON"), "CON-project");
});

test("DTS Explorer launch accepts only the typed executable and loopback WebUI", () => {
  const launch = normalizedDtsExplorerLaunch({
    executablePath: "D:\\DTS\\DTS Explorer.exe",
    launchUrl:
      "http://127.0.0.1:54321/#session=session-1&bootstrap=nonce-1",
    sessionId: "session-1",
  });
  assert.equal(launch.sessionId, "session-1");
  assert.equal(launch.executablePath, "D:\\DTS\\DTS Explorer.exe");
  assert.throws(
    () => normalizedDtsExplorerLaunch({
      executablePath: "D:\\DTS\\cmd.exe",
      launchUrl:
        "http://127.0.0.1:54321/#session=session-1&bootstrap=nonce-1",
      sessionId: "session-1",
    }),
    /executable is invalid/,
  );
  assert.throws(
    () => normalizedDtsExplorerLaunch({
      executablePath: "D:\\DTS\\DTS Explorer.exe",
      launchUrl: "https://example.com/#session=session-1&bootstrap=nonce-1",
      sessionId: "session-1",
    }),
    /WebUI URL is invalid/,
  );
});

test("desktop startup does not block Electron ready with top-level await", async () => {
  const source = await readFile(new URL("../src/main.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(source, /await\s+app\.whenReady\s*\(/);
  assert.match(source, /app\.whenReady\(\)\s*\n\s*\.then\s*\(/);
});

test("desktop shell removes the native application menu", async () => {
  const source = await readFile(new URL("../src/main.mjs", import.meta.url), "utf8");
  assert.match(source, /Menu\.setApplicationMenu\(null\)/);
  assert.match(source, /autoHideMenuBar:\s*true/);
});

test("the native window uses VerdFlow's icon and retains its product title", async () => {
  const source = await readFile(new URL("../src/main.mjs", import.meta.url), "utf8");
  assert.match(source, /icon:\s*windowIconPath/);
  assert.match(source, /title:\s*"VerdFlow Studio"/);
  assert.match(source, /page-title-updated/);
  assert.match(source, /resourcesPath, "branding"/);
});

test("development strategy directory install keeps its dedicated dialog", async () => {
  const source = await readFile(new URL("../src/main.mjs", import.meta.url), "utf8");
  assert.match(source, /selectStrategyPackageDirectory/);
  assert.match(source, /title:\s*"选择 VerdFlow 策略包目录"/);
  assert.match(source, /properties:\s*\["openDirectory"\]/);
});

test("resource package import has a dedicated multi-selection channel", async () => {
  const [mainSource, preloadSource, channelSource] = await Promise.all([
    readFile(new URL("../src/main.mjs", import.meta.url), "utf8"),
    readFile(new URL("../src/preload.cjs", import.meta.url), "utf8"),
    readFile(new URL("../src/channels.mjs", import.meta.url), "utf8"),
  ]);
  assert.match(channelSource, /selectSources:\s*"verdflow:select-sources"/);
  assert.match(preloadSource, /selectSourceFiles:\s*\(extensions\)/);
  assert.match(preloadSource, /webUtils\.getPathForFile\(file\)/);
  assert.match(mainSource, /channels\.selectSources/);
  assert.match(mainSource, /\["openFile", "multiSelections"\]/);
});

test("desktop export download streams through node http and verifies length", async () => {
  const payload = Buffer.from("<Instances><Instance /></Instances>", "utf8");
  const server = http.createServer((_request, response) => {
    response.writeHead(200, {
      "Content-Type": "application/xml",
      "Content-Length": String(payload.length),
    });
    response.end(payload);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const destination = path.join(
    os.tmpdir(),
    `verdflow-electron-download-${process.pid}-${Date.now()}.xml`,
  );
  try {
    await downloadLocalApiFile({
      target: new URL(`http://127.0.0.1:${address.port}/export.xml`),
      headers: { Accept: "application/xml" },
      destination,
    });
    assert.deepEqual(await readFile(destination), payload);
  } finally {
    server.close();
    await rm(destination, { force: true });
  }
});
