import assert from "node:assert/strict";
import test from "node:test";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

test("Windows helper replaces only isolated program files after parent exits and restarts the new program", { skip: process.platform !== "win32", timeout: 30000 }, async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdflow-swap-test-"));
  const id = "b".repeat(32), install = path.join(root, "application"), stage = path.join(root, `.verdflow-update-${id}`), job = path.join(root, "job");
  const powershell = path.join(process.env.SystemRoot, "System32/WindowsPowerShell/v1.0/powershell.exe");
  let parent, helper;
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
    parent = spawn(process.execPath, ["-e", "setTimeout(()=>{},60000)"], { windowsHide: true, stdio: "ignore" });
    await once(parent, "spawn");
    const planPath = path.join(job, "plan.json");
    await writeFile(planPath, JSON.stringify({ id, installRoot: install, stageRoot: stage, targetVersion: "0.4.0", parentPid: parent.pid }));
    helper = spawn(powershell, ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", fileURLToPath(new URL("../src/apply-update.ps1", import.meta.url)), "-PlanPath", planPath], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"], cwd: job });
    let errors = ""; helper.stderr.on("data", chunk => { errors += chunk; }); helper.stdout.resume();
    const completed = once(helper, "exit");
    await until(async () => (await readFile(path.join(job, "helper-ready"), "utf8")).trim() === String(parent.pid));
    assert.equal(await readFile(path.join(install, "old.txt"), "utf8"), "old");
    const stopped = once(parent, "exit"); parent.kill(); await stopped;
    const [code] = await completed;
    assert.equal(code, 0, errors);
    await until(async () => await readFile(path.join(install, "launched.txt"), "utf8") === "started");
    assert.equal(await readFile(path.join(root, `.verdflow-backup-${id}`, "old.txt"), "utf8"), "old");
    assert.equal(await readFile(path.join(root, "userdata.txt"), "utf8"), "retained");
    assert.equal(JSON.parse(await readFile(path.join(job, "result.json"), "utf8")).status, "applied");
    await assert.rejects(readFile(path.join(install, "old.txt")));
  } finally {
    for (const child of [parent, helper]) if (child && child.exitCode === null && child.signalCode === null) { const stopped = once(child, "exit"); child.kill(); await stopped; }
    if (path.dirname(root) === os.tmpdir() && path.basename(root).startsWith("verdflow-swap-test-")) await rm(root, { recursive: true });
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
