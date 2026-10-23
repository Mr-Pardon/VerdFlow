import { copyFile, readFile, rm } from "node:fs/promises";
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
  const powershell = path.join(process.env.SystemRoot || "C:\\Windows", "System32/WindowsPowerShell/v1.0/powershell.exe");
  const child = spawn(powershell, ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", copiedScript, "-PlanPath", planPath], {
    detached: true, windowsHide: true, cwd: directory, stdio: "ignore",
  });
  let failed = null;
  child.once("error", error => { failed = error; });
  try {
    const deadline = Date.now() + 10_000;
    while (Date.now() < deadline) {
      if (failed) throw failed;
      if (child.exitCode !== null || child.signalCode !== null) throw new Error("更新器启动失败，程序保持运行");
      try { if ((await readFile(path.join(directory, "helper-ready"), "utf8")).trim() === String(process.pid)) { child.unref(); return; } }
      catch (error) { if (error.code !== "ENOENT") throw error; }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error("更新器未能接管，程序保持运行");
  } catch (error) { child.kill(); throw error; }
}
