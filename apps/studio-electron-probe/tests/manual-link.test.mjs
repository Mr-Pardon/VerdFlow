import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { manualWindowOpenHandler, RESOURCE_CENTER_URL, USER_MANUAL_URL } from "../src/manual-link.mjs";

const settle = () => new Promise(resolve => setImmediate(resolve));

test("resource detail links open in the browser without allowing arbitrary destinations", async () => {
  const opened = [];
  const handler = manualWindowOpenHandler({ openExternal: async url => opened.push(url), onError: assert.fail });
  const page = "https://mr-pardon.github.io/verdflow-resources/?type=assets&id=dts-library";
  for (const url of [page, "https://mr-pardon.github.io/verdflow-resources/evil", "https://evil.test/verdflow-resources/", "https://user@mr-pardon.github.io/verdflow-resources/"]) handler({ url });
  await settle();
  assert.deepEqual(opened, [page]);
});

test("the manual opens externally without creating an Electron window", async () => {
  const opened = [];
  const handler = manualWindowOpenHandler({
    openExternal: async url => opened.push(url), onError: assert.fail,
  });
  assert.deepEqual(handler({ url: USER_MANUAL_URL }), { action: "deny" });
  await settle();
  assert.deepEqual(opened, [USER_MANUAL_URL]);
});

test("other websites, protocols and modified manual addresses remain blocked", async () => {
  const handler = manualWindowOpenHandler({ openExternal: assert.fail, onError: assert.fail });
  for (const url of [
    "https://example.com/", "file:///C:/Windows/system32/cmd.exe", "javascript:alert(1)",
    "http://mr-pardon.github.io/verdflow-site/", `${USER_MANUAL_URL}?redirect=other`,
    "https://mr-pardon.github.io.evil.test/verdflow-site/", "https://mr-pardon.github.io/other/",
  ]) assert.deepEqual(handler({ url }), { action: "deny" });
  await settle();
});

test("browser launch errors reach the user-facing error handler", async () => {
  let failure;
  const handler = manualWindowOpenHandler({
    openExternal: async () => { throw new Error("No browser available"); },
    onError: error => { failure = error; },
  });
  assert.deepEqual(handler({ url: USER_MANUAL_URL }), { action: "deny" });
  await settle();
  assert.equal(failure.message, "No browser available");
});

test("the Studio logo uses the exact approved manual address in a separate context", async () => {
  const source = await readFile(new URL("../../studio/src/components/AppShell.vue", import.meta.url), "utf8");
  const link = source.match(/<a\b[^>]*class="brand"[^>]*>/)?.[0];
  assert.ok(link);
  assert.ok(link.includes(`href="${USER_MANUAL_URL}"`));
  assert.match(link, /target="_blank"/);
  assert.match(link, /rel="noopener noreferrer"/);
});

test("the sidebar resource center opens the approved page externally", async () => {
  const source = await readFile(new URL("../../studio/src/components/SideNavigation.vue", import.meta.url), "utf8");
  const link = source.match(/<a\b[^>]*class="nav-item nav-resource-center"[^>]*>/)?.[0];
  assert.ok(link);
  assert.ok(link.includes(`href="${RESOURCE_CENTER_URL}"`));
  assert.match(link, /target="_blank"/);
  assert.match(link, /rel="noopener noreferrer"/);
  const opened = [];
  const handler = manualWindowOpenHandler({ openExternal: async url => opened.push(url), onError: assert.fail });
  assert.deepEqual(handler({ url: RESOURCE_CENTER_URL }), { action: "deny" });
  await settle();
  assert.deepEqual(opened, [RESOURCE_CENTER_URL]);
});
