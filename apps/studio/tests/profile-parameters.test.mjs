import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { createServer } from "vite";
import vue from "@vitejs/plugin-vue";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)), configFile: false,
  plugins: [vue()],
  cacheDir: fileURLToPath(new URL("../../../tests/.tmp/profile-parameters-vite", import.meta.url)),
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true, hmr: false, ws: false },
});
const parameters = [
  { key: "density", default: 5, minimum: 1, maximum: 10, group: "quick", visible_for_roles: ["tree"] },
  { key: "spacing", default: 3, minimum: 1, maximum: 10, group: "advanced" },
];
const strategy = { strategy_id: "test", strategy_version: "1", package_hash: "test-hash", executable: true };
const assignments = { tree: { enabled: true, candidates: [{ asset_id: "oak", weight: 1, scale: { mode: "fixed", value: 1 } }] } };
const details = Object.fromEntries(["preset", "pure", "empty"].map(id => [id, {
  summary: { profile_id: id, strategy_id: "test", strategy_version: "1", display_name: id, catalog_id: null, generation: 1 },
  assignments, catalog_drift: {}, drift_count: 0,
  strategy_parameters: id === "preset" ? { density: 8 } : id === "empty" ? {} : null,
}]));
let savedBody;
globalThis.window = { verdflowPlatform: {
  getLocalApiConnection: async () => ({ ok: true, value: { managementAvailable: true } }),
  requestLocalApi: async request => {
    const path = new URL(request.path, "http://test").pathname;
    let value;
    if (path === "/api/v1/strategies") value = { items: [strategy] };
    else if (path === "/api/v1/asset-catalogs") value = { items: [] };
    else if (path === "/api/v1/strategies/test-hash") value = {
      summary: strategy, roles: [{ role_key: "tree", required: true }], generation_parameters: parameters, default_random_seed: 42,
    };
    else if (path === "/api/v1/role-binding-profiles" && request.method === "POST") {
      savedBody = JSON.parse(request.body);
      value = { ...details.preset, strategy_parameters: savedBody.strategy_parameters };
    } else if (path === "/api/v1/role-binding-profiles") value = { items: Object.values(details).map(item => item.summary) };
    else value = details[path.split("/").at(-1)];
    assert.ok(value, path);
    return { ok: true, value: { status: 200, body: JSON.stringify(value) } };
  },
} };
const { usePlantingConfiguration } = await server.ssrLoadModule("/src/features/dts/usePlantingConfiguration.ts");
const { parameterPresetValues, parameterValuesValid, visibleParameters } = await server.ssrLoadModule("/src/features/shared/parameterPresets.ts");
const { parseRoleEnabled } = await server.ssrLoadModule("/src/features/shared/roleMapping.ts");
const { usePlantingPlans } = await server.ssrLoadModule("/src/features/library/usePlantingPlans.ts");
const input = { sessionId: "test", sceneRevisionId: "scene", regionSetId: "regions", selectedRegionIds: () => ["r"],
  currentExecution: () => null, onPreviewSubmitted() {} };

await test("every plan resets parameters; legacy mappings use exact strategy defaults", async () => {
  const model = usePlantingConfiguration(input);
  await model.initialize();
  await model.chooseStrategy("test-hash");
  model.strategyParameterValues.value = { density: 2, spacing: 9 };
  model.randomSeed.value = 123;
  await model.chooseProfile("preset");
  assert.deepEqual(model.strategyParameterValues.value, { density: 8, spacing: 3 });
  assert.equal(model.randomSeed.value, 123);
  model.setRoleEnabled({ tree: false });
  assert.deepEqual(model.visibleGenerationParameters.value.map(parameter => parameter.key), ["spacing"]);
  assert.equal(model.strategyParameterValues.value.density, 8);
  model.setRoleEnabled({ tree: true });
  assert.deepEqual(model.visibleGenerationParameters.value.map(parameter => parameter.key), ["density", "spacing"]);
  assert.equal(model.strategyParameterValues.value.density, 8);
  model.updateStrategyParameter("density", 7);
  await model.chooseProfile("pure");
  assert.deepEqual(model.strategyParameterValues.value, { density: 5, spacing: 3 });
  await model.chooseProfile("empty");
  assert.deepEqual(model.strategyParameterValues.value, { density: 5, spacing: 3 });
});

await test("plan selection consumes the resolved package instead of inferring version compatibility", async () => {
  const plans = usePlantingPlans();
  plans.strategies.value = [
    { ...strategy, strategy_version: "2.0.0", package_hash: "original" },
    { ...strategy, strategy_version: "2.10.0", package_hash: "resolved" },
    { ...strategy, strategy_version: "3.0.0", package_hash: "major" },
  ];
  const plan = { ...details.preset.summary, strategy_version: "2.0.0", resolved_strategy_hash: "resolved" };
  assert.equal(plans.strategyFor(plan).package_hash, "resolved");
  assert.equal(plan.strategy_version, "2.0.0");
  assert.deepEqual(parseRoleEnabled(assignments, [{ role_key: "tree" }, { role_key: "new_optional" }]),
    { tree: true, new_optional: false });
});

await test("historical batch restoration wins over the current Profile preset", async () => {
  const model = usePlantingConfiguration({ ...input, initialInput: {
    strategy_package_hash: "test-hash", random_seed: 99, profile_id: "preset", catalog_id: null,
    generation_args: { strategy_parameters: { density: 2, spacing: 4 } }, binding_overrides: {},
  } });
  await model.initialize();
  assert.deepEqual(model.strategyParameterValues.value, { density: 2, spacing: 4 });
  assert.equal(model.randomSeed.value, 99);
});

await test("only explicit saves persist values and never persist the random seed", async () => {
  const model = usePlantingConfiguration(input);
  await model.initialize();
  await model.chooseStrategy("test-hash");
  await model.chooseProfile("preset");
  model.updateStrategyParameter("density", 6);
  assert.equal(details.preset.strategy_parameters.density, 8);
  await model.saveProfile("Saved", false);
  assert.deepEqual(savedBody.strategy_parameters, { density: 6, spacing: 3 });
  assert.ok(!("random_seed" in savedBody));
  await model.saveProfile("Copy", true);
  assert.deepEqual(savedBody.strategy_parameters, { density: 6, spacing: 3 });
  assert.equal(savedBody.source_profile_id, 'preset');
  assert.equal(savedBody.profile_id, null);
  assert.equal(savedBody.expected_generation, 0);
});

test("parameter values are checked against strategy-owned bounds", () => {
  assert.deepEqual(parameterPresetValues(parameters, { density: 4 }), { density: 4, spacing: 3 });
  assert.equal(parameterValuesValid(parameters, { density: 11, spacing: 3 }), false);
  assert.equal(parameterValuesValid(parameters, { density: NaN, spacing: 3 }), false);
});
test("role-conditioned visibility is any-enabled, leaves global fields, and does not alter definitions", () => {
  const declarations = [
    { key: "global" },
    { key: "legacy", visible_for_roles: [] },
    { key: "shared", visible_for_roles: ["tree", "grass"] },
    { key: "tree_only", visible_for_roles: ["tree"] },
  ];
  const original = structuredClone(declarations);
  assert.deepEqual(visibleParameters(declarations, { tree: false }).map(parameter => parameter.key), ["global", "legacy", "shared"]);
  assert.deepEqual(visibleParameters(declarations, { tree: false, grass: false }).map(parameter => parameter.key), ["global", "legacy"]);
  assert.deepEqual(declarations, original);
});
await test("parameter editor keeps seed controls by default and advanced settings collapsed", async () => {
  const { default: Editor } = await server.ssrLoadModule("/src/components/StrategyParameterEditor.vue");
  const html = await renderToString(createSSRApp(Editor, {
    parameters, values: { density: 5, spacing: 3 }, randomSeed: 99, simplified: true,
  }));
  assert.ok(html.includes('class="strategy-seed-control"'));
  assert.ok(html.includes('class="strategy-advanced-settings"'));
  assert.ok(!/<details[^>]*\sopen(?:\s|=|>)/.test(html));
  assert.ok(html.includes("随机种子"));
  const profileHtml = await renderToString(createSSRApp(Editor, {
    parameters, values: { density: 5, spacing: 3 }, showSeed: false,
  }));
  assert.ok(!profileHtml.includes('class="strategy-seed-control"'));
  assert.ok(!profileHtml.includes("随机种子"));
});
await test("disabled plan roles replace vegetation controls without discarding their assignments", async () => {
  const { default: Workspace } = await server.ssrLoadModule("/src/components/PlanMappingWorkspace.vue");
  const mappings = { ground: [{ assetId: "grass", displayNameSnapshot: "Grass", weight: 1, scaleMode: "fixed", fixedScale: 1 }] };
  const original = structuredClone(mappings);
  const props = {
    roles: [{ role_key: "ground", display_name: "Ground", assignment_policy: "weighted_pool" }, { role_key: "tree", display_name: "Tree" }],
    assets: [{ asset_id: "grass", display_name: "Grass", thumbnail_resource_id: null, collection_ids: [], tags: [] }],
    collections: [], catalogs: [], mappings, enabled: { ground: false, tree: true },
  };
  const disabled = await renderToString(createSSRApp(Workspace, props));
  assert.ok(disabled.includes('class="role-disabled-state"'));
  assert.ok(disabled.includes("Ground已禁用"));
  assert.ok(!disabled.includes('class="vf-browser-grid"'));
  assert.ok(!disabled.includes('class="plan-selected-panel"'));
  const enabled = await renderToString(createSSRApp(Workspace, { ...props, enabled: { ground: true, tree: true } }));
  assert.ok(enabled.includes('class="vf-browser-grid"'));
  assert.ok(enabled.includes('class="plan-candidate"'));
  assert.deepEqual(mappings, original);
});
await test("guided role navigation shows only available previous and next roles", async () => {
  const { default: Editor } = await server.ssrLoadModule("/src/components/RoleMappingEditor.vue");
  const roles = [
    { role_key: "ground", display_name: "Ground" },
    { role_key: "shrub", display_name: "Shrub" },
    { role_key: "tree", display_name: "Tree" },
  ];
  const props = { roles, assets: [], mappings: {}, guided: true };
  const footer = async (extra) => {
    const html = await renderToString(createSSRApp(Editor, { ...props, ...extra }));
    return html.match(/<footer\b[^>]*class="role-guide-next"[\s\S]*?<\/footer>/)?.[0] ?? "";
  };
  const first = await footer({ activeRoleKey: "ground" });
  assert.ok(!first.includes("上一个："));
  assert.ok(first.includes("下一个：Shrub"));
  const middle = await footer({ activeRoleKey: "shrub", enabled: { ground: false } });
  assert.ok(middle.includes("上一个：Ground"));
  assert.ok(middle.includes("下一个：Tree"));
  const last = await footer({ activeRoleKey: "tree" });
  assert.ok(last.includes("上一个：Shrub"));
  assert.ok(!last.includes("下一个："));
  const locked = await footer({ activeRoleKey: "shrub", disabled: true });
  assert.equal((locked.match(/<button\b[^>]*\bdisabled/g) ?? []).length, 2);
  assert.equal(await footer({ roles: roles.slice(0, 1) }), "");
  assert.equal(await footer({ roles: [] }), "");
  assert.equal(await footer({ guided: false }), "");
  assert.equal(await footer({ activeRoleKey: "missing" }), first);
});
await server.close();
