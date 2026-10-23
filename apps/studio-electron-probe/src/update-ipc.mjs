import { channels } from "./channels.mjs";
import { assertUpdateIdle } from "./update-source.mjs";

export function registerUpdateHandlers({ ipcMain, BrowserWindow, dialog, updater, readTaskState, hasPendingMutations, setApplying, quit }) {
  let applying = false;
  const handle = (channel, action) => ipcMain.handle(channel, async (event, value) => {
    const owner = BrowserWindow.fromWebContents(event.sender);
    if (!owner || event.senderFrame !== event.sender.mainFrame) return { ok: false, reason: "failed", message: "更新请求来源无效" };
    try { return { ok: true, value: await action(value, owner) }; }
    catch (error) { return { ok: false, reason: "failed", message: error instanceof Error ? error.message : "更新操作失败" }; }
  });
  handle(channels.updateStatus, () => updater.snapshot());
  handle(channels.updateCheck, () => updater.check());
  handle(channels.updateDownload, () => updater.download());
  handle(channels.updateCancel, () => updater.cancel());
  handle(channels.updatePreferences, value => updater.preferences(value));
  handle(channels.updateApply, async (_value, owner) => {
    if (applying) throw new Error("更新正在准备重启");
    if (updater.snapshot().stage !== "ready") throw new Error("更新包尚未准备好");
    applying = true;
    setApplying(true);
    try {
      if (hasPendingMutations()) throw new Error("仍有操作尚未完成，请稍后更新");
      await assertUpdateIdle(readTaskState);
      const result = await dialog.showMessageBox(owner, {
        type: "question", title: "更新 VerdFlow", message: `重启并更新至 v${updater.snapshot().latestVersion}？`,
        detail: "请先保存尚未保存的配置。程序会关闭后重新打开，项目、资产和种植方案将保留。",
        buttons: ["稍后", "重启并更新"], defaultId: 0, cancelId: 0,
      });
      if (result.response !== 1) return;
      if (hasPendingMutations()) throw new Error("仍有操作尚未完成，请稍后更新");
      await assertUpdateIdle(readTaskState);
      await updater.apply();
      quit();
    } finally {
      if (updater.snapshot().stage !== "applying") { applying = false; setApplying(false); }
    }
  });
}
