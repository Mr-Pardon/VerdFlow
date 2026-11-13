const liveStates = new Set(["starting", "waiting_for_explorer", "connected"]);

export const dtsLiveQuitPrompt = Object.freeze({
  type: "warning",
  title: "退出 VerdFlow",
  message: "实时种植服务仍在运行",
  detail: "退出 VerdFlow 会停止实时种植服务，并关闭由 VerdFlow 启动的 DTS Explorer。请先在 Explorer 中保存工程。",
  buttons: ["返回保存", "仍然退出"],
  defaultId: 0,
  cancelId: 0,
});

export function createDtsLiveQuitGuard({ readStatus, hasRunningExplorer, confirm, quit, onError }) {
  let checking = false;
  let approved = false;

  return (event, owner) => {
    if (approved) return false;
    event.preventDefault();
    if (!checking) {
      checking = true;
      void (async () => {
        let warn;
        try {
          warn = liveStates.has((await readStatus())?.state);
        } catch (error) {
          onError(error);
          warn = hasRunningExplorer();
        }
        if (warn && !(await confirm(owner))) return;
        approved = true;
        quit();
      })().catch(onError).finally(() => { checking = false; });
    }
    return true;
  };
}
