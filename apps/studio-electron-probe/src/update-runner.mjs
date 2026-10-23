import { copyFile, open, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";

export const preparePortableUpdate = ({ python, script, planPath, signal }) => new Promise((resolve, reject) => {
  const child = spawn(python, ["-B", script, planPath], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let output = "", failure = null;
  const abort = () => { failure = new Error("已取消更新下载"); child.kill(); };
  const timer = setTimeout(() => { failure = new Error("更新包校验耗时过长，请重试"); child.kill(); }, 300_000);
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  child.stdout.on("data", chunk => { output = (output + chunk.toString()).slice(-16_384); });
  child.stderr.on("data", () => undefined);
  child.on("error", error => { failure = error; });
  child.on("close", code => {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    if (failure) reject(failure);
    else if (code !== 0 || !output.includes("portable-update-ready")) reject(new Error("更新包校验失败，未替换现有程序"));
    else resolve();
  });
});

export async function launchPortableUpdate({ script, planPath }) {
  const directory = path.dirname(planPath);
  const copiedScript = path.join(directory, "apply-update.ps1");
  await copyFile(script, copiedScript);
  await rm(path.join(directory, "helper-ready"), { force: true });
  const acceptedPath = path.join(directory, "helper-accepted.json");
  await rm(acceptedPath, { force: true });
  await rm(path.join(directory, "helper-launch.json"), { force: true });
  const powershell = path.join(process.env.SystemRoot || "C:\\Windows", "System32/WindowsPowerShell/v1.0/powershell.exe");
  const output = await open(path.join(directory, "helper-launch.log"), "w");
  let child;
  let completed = Promise.resolve();
  let failed = null;
  try {
    // Windows PowerShell needs a hidden Start-Process bootstrap, not Node's
    // detached console flags, to survive the application closing reliably.
    child = spawn(powershell, ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", copiedScript, "-PlanPath", planPath, "-Launch"], {
      detached: false, windowsHide: true, cwd: directory, stdio: ["ignore", output.fd, output.fd],
    });
    child.once("error", error => { failed = error; });
    completed = new Promise(resolve => child.once("close", resolve));
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      if (failed) throw failed;
      if (child.signalCode !== null || (child.exitCode !== null && child.exitCode !== 0)) throw new Error("更新器启动失败，程序保持运行");
      if (child.exitCode === 0) {
        const handshake = await readFile(path.join(directory, "helper-ready"), "utf8").catch(error => { if (error.code === "ENOENT") return ""; throw error; });
        if (handshake.trim() !== String(process.pid)) throw new Error("更新器启动失败，程序保持运行");
        const plan = JSON.parse(await readFile(planPath, "utf8"));
        await writeFile(acceptedPath, JSON.stringify({ id: plan.id, parentPid: process.pid }), "utf8");
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error("更新器未能接管，程序保持运行");
  } catch (error) {
    await rm(acceptedPath, { force: true }).catch(() => undefined);
    child?.kill();
    await completed;
    await writeFile(path.join(directory, "helper-launch.json"), JSON.stringify({
      status: "failed", exitCode: child?.exitCode ?? null, signal: child?.signalCode ?? null,
      message: error instanceof Error ? error.message : "Update helper failed",
    }), "utf8").catch(() => undefined);
    throw error;
  } finally { await output.close(); }
}
