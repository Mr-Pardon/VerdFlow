import assert from "node:assert/strict";
import test from "node:test";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { createRequire } from "node:module";
import { copyFile, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { launchPortableUpdate } from "../src/update-runner.mjs";

for (const [runtime, accept, rollback = false] of [["node", true], ["electron", true], ["electron-app", true], ["node", false], ["node", true, true]]) test(`Windows ${runtime} update: acceptance=${accept}, rollback=${rollback}`, { skip: process.platform !== "win32", timeout: 30000 }, async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdflow-swap-test-"));
  const id = "b".repeat(32), install = path.join(root, "application"), stage = path.join(root, `.verdflow-update-${id}`), job = path.join(root, "job ' \u6d4b\u8bd5");
  const powershell = path.join(process.env.SystemRoot, "System32/WindowsPowerShell/v1.0/powershell.exe");
  let parent;
  let handedOff = false;
  try {
    for (const directory of [install, stage, job]) await mkdir(directory);
    await writeFile(path.join(install, "old.txt"), "old");
    await writeFile(path.join(root, "userdata.txt"), "retained");
    await writeFile(path.join(stage, ".verdflow-update-ready.json"), JSON.stringify({ id, version: "0.4.0" }));
    const executable = path.join(stage, "VerdFlow Studio.exe");
    const source = 'using System; using System.IO; public class TestApp { public static void Main() { File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "launched.txt"), "started"); } }';
    const command = `Add-Type -TypeDefinition '${source}' -OutputAssembly '${executable.replaceAll("'", "''")}' -OutputType WindowsApplication`;
    const compiled = spawnSync(powershell, ["-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(command, "utf16le").toString("base64")], { windowsHide: true, encoding: "utf8" });
    assert.equal(compiled.status, 0, compiled.stderr);
    if (rollback) await copyFile(executable, path.join(install, "VerdFlow Studio.exe"));
    const planPath = path.join(job, "plan.json");
    await writeFile(planPath, JSON.stringify({ id, installRoot: install, stageRoot: stage, targetVersion: "0.4.0" }));
    const ownerScript = `
      import { readFile, writeFile, rm } from "node:fs/promises";
      import { launchPortableUpdate } from ${JSON.stringify(new URL("../src/update-runner.mjs", import.meta.url).href)};
      ${runtime === "electron-app" ? `import { app } from "electron"; app.setPath("userData", ${JSON.stringify(path.join(job, "electron-data"))}); void app.whenReady().then(async () => {` : ""}
      const planPath = ${JSON.stringify(planPath)};
      ${runtime === "electron-app" ? `const quitTimer = setInterval(async () => { if (await readFile(${JSON.stringify(path.join(job, "quit"))}, "utf8").catch(() => "") === "quit") { clearInterval(quitTimer); app.quit(); } }, 50);` : `process.stdin.resume(); process.stdin.once("data", () => process.stdin.pause());`}
      try {
        const plan = JSON.parse(await readFile(planPath, "utf8"));
        await writeFile(planPath, JSON.stringify({ ...plan, parentPid: process.pid }));
        await launchPortableUpdate({ script: ${JSON.stringify(fileURLToPath(new URL("../src/apply-update.ps1", import.meta.url)))}, planPath });
        if (!${accept}) await rm(${JSON.stringify(path.join(job, "helper-accepted.json"))});
        await writeFile(${JSON.stringify(path.join(job, "owner-ready"))}, "ready");
      } catch (error) { console.error(error); process.stdin.pause(); process.exitCode = 1; ${runtime === "electron-app" ? "app.exit(1);" : ""} }
      ${runtime === "electron-app" ? "});" : ""}
    `;
    const executablePath = runtime.startsWith("electron") ? createRequire(import.meta.url)("electron") : process.execPath;
    const env = { ...process.env };
    delete env.ELECTRON_RUN_AS_NODE;
    if (runtime === "electron") env.ELECTRON_RUN_AS_NODE = "1";
    let args = ["--input-type=module", "-e", ownerScript];
    if (runtime === "electron-app") {
      await writeFile(path.join(job, "package.json"), JSON.stringify({ name: "verdflow-update-test", version: "0.0.0", main: "owner.mjs" }));
      await writeFile(path.join(job, "owner.mjs"), ownerScript);
      args = [job];
    }
    parent = spawn(executablePath, args, { windowsHide: true, stdio: ["pipe", "pipe", "pipe"], cwd: job, env });
    let errors = "";
    parent.stderr.on("data", chunk => { errors += chunk; });
    parent.stdout.resume();
    const completed = once(parent, "exit");
    await until(async () => {
      assert.equal(parent.exitCode, null, errors);
      return await readFile(path.join(job, "owner-ready"), "utf8") === "ready";
    });
    handedOff = true;
    assert.equal((await readFile(path.join(job, "helper-ready"), "utf8")).trim(), String(parent.pid));
    assert.equal(await readFile(path.join(install, "old.txt"), "utf8"), "old");
    if (rollback) await rm(stage, { recursive: true });
    if (runtime === "electron-app") await writeFile(path.join(job, "quit"), "quit");
    else parent.stdin.end("quit");
    const [code] = await completed;
    assert.equal(code, 0, errors);
    if (accept && !rollback) {
      await until(async () => await readFile(path.join(install, "launched.txt"), "utf8") === "started");
      assert.equal(await readFile(path.join(root, `.verdflow-backup-${id}`, "old.txt"), "utf8"), "old");
      assert.equal(JSON.parse(await readFile(path.join(job, "result.json"), "utf8")).status, "applied");
      await assert.rejects(readFile(path.join(install, "old.txt")));
    } else {
      await until(async () => JSON.parse(await readFile(path.join(job, "result.json"), "utf8")).status === "failed");
      assert.equal(await readFile(path.join(install, "old.txt"), "utf8"), "old");
      if (rollback) await until(async () => await readFile(path.join(install, "launched.txt"), "utf8") === "started");
      else await assert.rejects(readFile(path.join(install, "launched.txt")));
      await assert.rejects(readFile(path.join(root, `.verdflow-backup-${id}`, "old.txt")));
    }
    assert.equal(await readFile(path.join(root, "userdata.txt"), "utf8"), "retained");
  } finally {
    if (parent && parent.exitCode === null && parent.signalCode === null) { const stopped = once(parent, "exit"); parent.kill(); await stopped; }
    if (handedOff) await until(async () => Boolean(await readFile(path.join(job, "result.json"), "utf8")));
    if (path.dirname(root) === os.tmpdir() && path.basename(root).startsWith("verdflow-swap-test-")) await rm(root, { recursive: true, maxRetries: 20, retryDelay: 100 });
  }
});

test("failed Windows handoff retains diagnostics without accepting or replacing the program", { skip: process.platform !== "win32", timeout: 15000 }, async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdflow-swap-test-"));
  const id = "c".repeat(32), installRoot = path.join(root, "application"), stageRoot = path.join(root, `.verdflow-update-${id}`), job = path.join(root, "job");
  try {
    for (const directory of [installRoot, stageRoot, job]) await mkdir(directory);
    await writeFile(path.join(installRoot, "original"), "retained");
    await writeFile(path.join(stageRoot, ".verdflow-update-ready.json"), JSON.stringify({ id, version: "wrong-version" }));
    const planPath = path.join(job, "plan.json");
    await writeFile(planPath, JSON.stringify({ id, installRoot, stageRoot, targetVersion: "1.0.0", parentPid: process.pid }));
    await assert.rejects(launchPortableUpdate({ script: fileURLToPath(new URL("../src/apply-update.ps1", import.meta.url)), planPath }), /更新器启动失败/);
    const failure = JSON.parse(await readFile(path.join(job, "helper-launch.json"), "utf8"));
    assert.equal(failure.status, "failed");
    assert.equal(failure.exitCode, 1);
    assert.match(await readFile(path.join(job, "helper-launch.log"), "utf8"), /Update is not prepared/);
    assert.equal(await readFile(path.join(installRoot, "original"), "utf8"), "retained");
    await assert.rejects(readFile(path.join(job, "helper-accepted.json")));
  } finally {
    if (path.dirname(root) === os.tmpdir() && path.basename(root).startsWith("verdflow-swap-test-")) await rm(root, { recursive: true, maxRetries: 20, retryDelay: 100 });
  }
});

async function until(check) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    try { if (await check()) return; } catch (error) { if (error.code !== "ENOENT") throw error; }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error("Update helper did not complete its handshake or restart");
}
