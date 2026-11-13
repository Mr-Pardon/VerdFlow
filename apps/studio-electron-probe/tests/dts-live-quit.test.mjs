import assert from "node:assert/strict";
import { setImmediate as flush } from "node:timers/promises";
import test from "node:test";
import { createDtsLiveQuitGuard, dtsLiveQuitPrompt } from "../src/dts-live-quit.mjs";

function setup({ readStatus, hasRunningExplorer = () => false, confirm = async () => true }) {
  const seen = { prompts: [], errors: [], quits: 0, reads: 0 };
  const guard = createDtsLiveQuitGuard({
    readStatus: () => { seen.reads++; return readStatus(); },
    hasRunningExplorer,
    confirm: (owner) => { seen.prompts.push(owner); return confirm(); },
    quit: () => { seen.quits++; },
    onError: (error) => { seen.errors.push(error.message); },
  });
  const attempt = (owner = "window") => {
    const event = { blocked: false, preventDefault() { this.blocked = true; } };
    assert.equal(guard(event, owner), event.blocked);
    return event.blocked;
  };
  return { attempt, seen };
}

test("active Live states warn before quitting; stopped or failed states do not", async () => {
  for (const state of ["starting", "waiting_for_explorer", "connected", "stopped", "failed"]) {
    const { attempt, seen } = setup({ readStatus: async () => ({ state }) });
    assert.equal(attempt(), true);
    await flush();
    assert.equal(seen.prompts.length, ["stopped", "failed"].includes(state) ? 0 : 1);
    assert.equal(seen.quits, 1);
    assert.equal(attempt(), false);
    assert.equal(seen.reads, 1);
  }
});

test("returning to save keeps the app open; repeated closes cannot stack dialogs", async () => {
  let choose;
  const { attempt, seen } = setup({
    readStatus: async () => ({ state: "connected" }),
    confirm: () => new Promise(resolve => { choose = resolve; }),
  });
  assert.equal(attempt("studio"), true);
  assert.equal(attempt("second-close"), true);
  await flush();
  assert.deepEqual(seen.prompts, ["studio"]);
  choose(false);
  await flush();
  assert.equal(seen.quits, 0);
  assert.equal(attempt("studio"), true);
  await flush();
  choose(true);
  await flush();
  assert.equal(seen.quits, 1);
  assert.equal(attempt(), false);
  assert.equal(dtsLiveQuitPrompt.buttons[dtsLiveQuitPrompt.defaultId], "返回保存");
  assert.equal(dtsLiveQuitPrompt.cancelId, dtsLiveQuitPrompt.defaultId);
});

test("an unreachable service still warns when Studio knows it launched a running Explorer", async () => {
  for (const running of [true, false]) {
    const { attempt, seen } = setup({
      readStatus: async () => { throw new Error("service offline"); },
      hasRunningExplorer: () => running,
      confirm: async () => false,
    });
    attempt();
    await flush();
    assert.deepEqual(seen.errors, ["service offline"]);
    assert.equal(seen.prompts.length, running ? 1 : 0);
    assert.equal(seen.quits, running ? 0 : 1);
  }
});
