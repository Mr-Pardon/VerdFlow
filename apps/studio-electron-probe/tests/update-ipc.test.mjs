import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { channels } from "../src/channels.mjs";
import { registerUpdateHandlers } from "../src/update-ipc.mjs";

function fixture({ pending = false, live = "stopped", confirm = 1, launchFailure = false } = {}) {
  const handlers = new Map(), events = [];
  const frame = {}, sender = { mainFrame: frame }, state = { stage: "ready", latestVersion: "0.4.0" };
  registerUpdateHandlers({
    ipcMain: { handle: (channel, action) => handlers.set(channel, action) },
    BrowserWindow: { fromWebContents: value => value === sender ? {} : null },
    dialog: { showMessageBox: async () => { events.push("confirm"); return { response: confirm }; } },
    updater: { snapshot: () => state, apply: async () => { if (launchFailure) throw new Error("helper failed"); state.stage = "applying"; events.push("handoff"); } },
    readTaskState: async url => { events.push("read"); return url.endsWith("status") ? { state: live } : { items: [] }; },
    hasPendingMutations: () => pending, setApplying: value => events.push(value ? "lock" : "unlock"), quit: () => events.push("quit"),
  });
  return { handlers, events, call: (event = { sender, senderFrame: frame }) => handlers.get(channels.updateApply)(event) };
}

test("update restart locks mutations, rechecks idle after confirmation, hands off then quits", async () => {
  const f = fixture();
  assert.equal((await f.call()).ok, true);
  assert.deepEqual(f.events, ["lock", "read", "read", "confirm", "read", "read", "handoff", "quit"]);
});

for (const options of [{ pending: true }, { live: "connected" }, { confirm: 0 }, { launchFailure: true }]) test(`update never quits on unsafe or declined handoff: ${JSON.stringify(options)}`, async () => {
  const f = fixture(options);
  await f.call();
  assert.equal(f.events.includes("quit"), false);
  assert.equal(f.events.at(-1), "unlock");
});

test("foreign web contents and child frames cannot initiate updates", async () => {
  const f = fixture();
  assert.equal((await f.call({ sender: { mainFrame: {} }, senderFrame: {} })).ok, false);
  assert.deepEqual(f.events, []);
});

test("preload exposes bounded commands and removes the exact update listener", async () => {
  const calls = [], listeners = new Map();
  let platform;
  vm.runInNewContext(await readFile(new URL("../src/preload.cjs", import.meta.url), "utf8"), { require: () => ({
    contextBridge: { exposeInMainWorld: (_name, value) => { platform = value; } }, webUtils: {},
    ipcRenderer: {
      invoke: (...args) => { calls.push(args); },
      on: (channel, action) => listeners.set(channel, action),
      removeListener: (channel, action) => { assert.equal(listeners.get(channel), action); listeners.delete(channel); },
    },
  }) });
  let received;
  const unsubscribe = platform.applicationUpdates.subscribe(value => { received = value; });
  listeners.get(channels.updateState)({}, { stage: "ready" });
  assert.equal(received.stage, "ready");
  unsubscribe();
  assert.equal(listeners.size, 0);
  platform.applicationUpdates.download("https://untrusted.test/file");
  assert.deepEqual(Array.from(calls[0]), [channels.updateDownload]);
});
