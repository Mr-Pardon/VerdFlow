import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { createServer } from "vite";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  configFile: false,
  cacheDir: fileURLToPath(new URL("../../../tests/.tmp/role-guide-vite", import.meta.url)),
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true, hmr: false, ws: false },
});
const modulePath = fileURLToPath(new URL("../../../packages/role-guide-ui/src/types.ts", import.meta.url));
const { roleGroups, roleColor, withRoleGroupEnabled } = await server.ssrLoadModule(`/@fs/${modulePath.replaceAll("\\", "/")}`);
globalThis.window = { verdflowPlatform: {} };
const { useStrategyLibrary } = await server.ssrLoadModule("/src/features/library/useStrategyLibrary.ts");

test("role presentation groups retain exact role identity and do not change configuration", () => {
  const roles = [
    { role_key: "tree", display_name: "沿线乔木", guide: { group_id: "trees", group_name: "所有宽度" } },
    { role_key: "left", display_name: "左排灌木", guide: { group_id: "double", group_name: "中等宽度绿带" } },
    { role_key: "right", display_name: "右排灌木", guide: { group_id: "double", group_name: "中等宽度绿带" } },
  ];
  const original = structuredClone(roles);
  const groups = roleGroups(roles);
  assert.deepEqual(groups.map(group => group.id), ["trees", "double"]);
  assert.deepEqual(groups[1].roles.map(role => role.role_key), ["left", "right"]);
  assert.equal(groups[0].roles[0], roles[0]);
  assert.deepEqual(roles, original);
});

test("unannotated strategies remain one plain group with their original names", () => {
  const roles = [{ role_key: "existing", display_name: "原名", guide: null }];
  const groups = roleGroups(roles);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].name, "");
  assert.equal(groups[0].roles[0].display_name, "原名");
  assert.deepEqual(roleGroups([]), []);
});

test("diagram and role navigation use the same deterministic colors", () => {
  const roles = [{ role_key: "ground" }, { role_key: "main" }];
  assert.equal(roleColor(roles, "ground"), roleColor(roles, "ground"));
  assert.notEqual(roleColor(roles, "ground"), roleColor(roles, "main"));
  assert.match(roleColor([], "unknown"), /^#[\da-f]{6}$/);
});

test("strategy library sorts names and versions without builtin ID preference", () => {
  const library = useStrategyLibrary();
  library.strategies.value = [
    { strategy_id: "universal_natural_grove", display_name: "Zebra", strategy_version: "2.0.0" },
    { strategy_id: "new_user_strategy", display_name: "Alpha", strategy_version: "1.0.0" },
    { strategy_id: "new_user_strategy", display_name: "Alpha", strategy_version: "1.0.1" },
  ];
  assert.deepEqual(library.groups.value.map(group => group.displayName), ["Alpha", "Zebra"]);
  assert.deepEqual(library.groups.value[0].versions.map(item => item.strategy_version), ["1.0.1", "1.0.0"]);
});

test("group enablement is atomic, preserves other roles and refuses all-off", () => {
  const roles = ['ground_a', 'ground_b', 'tree'].map(role_key => ({ role_key }));
  const original = { ground_a: true, ground_b: true, tree: true };
  const disabled = withRoleGroupEnabled(roles, original, ['ground_a', 'ground_b'], false);
  assert.deepEqual(disabled, { ground_a: false, ground_b: false, tree: true });
  assert.equal(withRoleGroupEnabled(roles, disabled, ['tree'], false), disabled);
  assert.deepEqual(withRoleGroupEnabled(roles, disabled, ['ground_a', 'ground_b'], true), original);
  assert.deepEqual(original, { ground_a: true, ground_b: true, tree: true });
});
await test("bulk trust excludes revoked packages and retains partial successes", async () => {
  const calls = [];
  window.verdflowPlatform.requestLocalApi = async request => {
    const hash = request.path.split('/').at(-2);
    calls.push(hash);
    if (hash === 'fails') throw new Error('Trust failed');
    return { ok: true, value: { status: 200, body: JSON.stringify({ package_hash: hash, trust_state: 'trusted', executable: true }) } };
  };
  const model = useStrategyLibrary();
  model.managementAvailable.value = true;
  model.strategies.value = ['ok', 'fails', 'revoked', 'trusted'].map(package_hash => ({ package_hash, display_name: package_hash, strategy_version: '1', trust_state: ['trusted', 'revoked'].includes(package_hash) ? package_hash : null }));
  const targets = [...model.trustableStrategies.value];
  assert.deepEqual(targets.map(item => item.package_hash), ['ok', 'fails']);
  await model.trustAll(model.strategies.value);
  assert.deepEqual(calls, ['ok', 'fails']);
  assert.equal(model.strategies.value.find(item => item.package_hash === 'ok').trust_state, 'trusted');
  assert.match(model.errorMessage.value, /fails/);
});
await server.close();
