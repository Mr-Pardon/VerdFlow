import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import { createServer } from "vite";
import vue from "@vitejs/plugin-vue";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)), configFile: false, plugins: [vue()],
  cacheDir: fileURLToPath(new URL("../../../tests/.tmp/plan-detail-names-vite", import.meta.url)),
  optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, hmr: false, ws: false },
});
const strategy = { strategy_id: "internal_strategy", strategy_version: "2.1.0", package_hash: "resolved", display_name: "层次生态群落" };
let catalogs = [{ catalog_id: "internal_catalog", display_name: "DTS 植被库" }, { catalog_id: "temporary_catalog", display_name: "项目临时植被库" }];
let assignments = {};
let failCatalogs = false;
let installed = true;
globalThis.window = { verdflowPlatform: {
  getLocalApiConnection: async () => ({ ok: true, value: { managementAvailable: true } }),
  requestLocalApi: async request => {
    const path = new URL(request.path, "http://fixture").pathname;
    let data;
    if (path === "/api/v1/role-binding-profiles/fixture") data = {
      summary: { profile_id: "fixture", strategy_id: strategy.strategy_id, strategy_version: "2.0.0", resolved_strategy_hash: "resolved", target_type: "dts", catalog_id: "internal_catalog" },
      assignments, assets: [], catalog_drift: {}, strategy_parameters: null,
    };
    else if (path === "/api/v1/strategies") data = { items: installed ? [strategy] : [] };
    else if (path === "/api/v1/strategies/resolved") data = { summary: strategy, roles: [], generation_parameters: [] };
    else if (path === "/api/v1/asset-catalogs") {
      if (failCatalogs) throw new Error("fixture catalog unavailable");
      data = { items: catalogs };
    } else throw new Error(path);
    return { ok: true, value: { status: 200, body: JSON.stringify(data) } };
  },
} };
const { useBindingProfileLibrary } = await server.ssrLoadModule("/src/features/library/useBindingProfileLibrary.ts");
let model;
await renderToString(createSSRApp({ setup() { model = useBindingProfileLibrary({ targetType: "dts", profileId: () => "fixture" }); return () => null; } }));

try {
  await test("detail names use resolved strategy and catalog metadata without changing the saved baseline", async () => {
    await model.initialize();
    assert.equal(model.strategyDisplayName.value, "层次生态群落");
    assert.equal(model.catalogDisplayName.value, "DTS 植被库");
    assert.equal(model.profileDetail.value.summary.strategy_version, "2.0.0");
  });
  await test("multi-catalog plans show all linked catalog names", async () => {
    assignments = { tree: { candidates: [{ catalog_link: { catalog_id: "temporary_catalog", asset_id: "oak" } }] } };
    await model.refreshTarget();
    assert.equal(model.catalogDisplayName.value, "DTS 植被库、项目临时植被库");
  });
  await test("removed or unreadable sources use readable fallback text, not internal IDs", async () => {
    catalogs = [];
    installed = false;
    await model.refreshTarget();
    assert.equal(model.strategyDisplayName.value, "策略暂不可用");
    assert.equal(model.catalogDisplayName.value, "来源资产库已移除");
    assert.ok(model.profileDetail.value);
    failCatalogs = true;
    await model.refreshTarget();
    assert.equal(model.catalogDisplayName.value, "来源资产库暂不可用");
    assert.equal(model.errorMessage.value, null);
  });
} finally { await server.close(); }
