import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { createServer } from "vite";
import { createRenderer, nextTick, ref } from "vue";

const root = fileURLToPath(new URL("../", import.meta.url));
const server = await createServer({
  root,
  configFile: false,
  cacheDir: fileURLToPath(new URL("../../../tests/.tmp/studio-state-vite", import.meta.url)),
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true, hmr: false, ws: false },
});
let respond;
globalThis.window = {
  verdflowPlatform: {
    getLocalApiConnection: async () => ({ ok: true, value: { managementAvailable: true } }),
    requestLocalApi: async (request) => ({
      ok: true,
      value: { status: 200, body: JSON.stringify(await respond(request)) },
    }),
  },
};
globalThis.document = {
  visibilityState: "visible",
  addEventListener() {},
  removeEventListener() {},
};
const { useDtsWorkspace } = await server.ssrLoadModule("/src/features/dts/useDtsWorkspace.ts");
const { usePlantingConfiguration } = await server.ssrLoadModule("/src/features/dts/usePlantingConfiguration.ts");
const { projectsStateKey } = await server.ssrLoadModule("/src/features/projects/state.ts");
const renderer = createRenderer({
  createComment: () => ({}),
  createElement: () => ({}),
  createText: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
  parentNode: () => null, nextSibling: () => null,
});
const mount = (setup) => {
  let state;
  const app = renderer.createApp({ setup() { state = setup(); return () => null; } });
  app.provide(projectsStateKey, {
    projects: ref([{ projectId: "project", sessionId: "session" }]),
    refresh: async () => {},
  });
  app.mount({});
  return { state, unmount: () => app.unmount() };
};
const flush = async () => {
  await new Promise((resolve) => setImmediate(resolve));
  await nextTick();
};
const execution = (status = "running", revision = 1) => ({
  execution_id: "preview", status, revision, stage: "generation",
  generated_point_set_id: null, asset_assignment_set_id: null,
});
const previewInput = {
  scene_revision_id: "scene", region_set_id: "regions", selected_region_ids: ["region-1"],
  strategy_package_hash: "strategy", random_seed: 42, target_type: "dts",
  catalog_id: null, profile_id: "profile", generation_args: {}, binding_overrides: {},
};
const strategy = { package_hash: "strategy", strategy_id: "strategy", strategy_version: "1", executable: true };
const profile = { profile_id: "profile", strategy_id: "strategy", strategy_version: "1", catalog_id: null };
const api = (current, summaryId = current?.execution_id ?? null) => async ({ path: rawPath }) => {
  const path = new URL(rawPath, "http://localhost").pathname;
  if (path.endsWith("/summary")) return { plant_run_execution_id: summaryId, execution_status: current?.status, scene_preparation_execution_id: null };
  if (path.endsWith("/tasks") || path.endsWith("/xml-outputs")) return { items: [] };
  if (path.endsWith("/planting-composition")) return { composition: null };
  if (path.endsWith("/input")) return previewInput;
  if (path.endsWith("/plant-previews/preview")) return current;
  if (path === "/api/v1/strategies") return { items: [strategy] };
  if (path === "/api/v1/strategies/strategy") return { summary: strategy, roles: [{ role_key: "tree", required: true }], generation_parameters: [] };
  if (path === "/api/v1/asset-catalogs") return { items: [] };
  if (path === "/api/v1/role-binding-profiles") return { items: [profile] };
  if (path === "/api/v1/role-binding-profiles/profile") return {
    summary: profile, assignments: { tree: { enabled: true, candidates: [{ asset_id: "oak", weight: 1, scale: { mode: "fixed", value: 1 } }] } },
  };
  throw new Error(`Unexpected API request: ${path}`);
};
const configuration = (current, onPreviewSubmitted = () => {}) => mount(() => usePlantingConfiguration({
  sessionId: "session", sceneRevisionId: "scene", regionSetId: "regions",
  selectedRegionIds: () => ["region-1"], initialInput: previewInput,
  currentExecution: () => current.value, onPreviewSubmitted,
}));

try {
  await test("planting execution survives panel and page lifecycle", async (t) => {
    await t.test("restored active execution stays locked after panel remount", async () => {
      respond = api(execution());
      const current = ref(execution());
      const first = configuration(current);
      await first.state.initialize();
      assert.equal(first.state.generating.value, true);
      assert.equal(first.state.canSubmit.value, false);
      first.unmount();
      const second = configuration(current);
      try {
        await second.state.initialize();
        assert.equal(second.state.mappingReady.value, true);
        assert.equal(second.state.canSubmit.value, false);
        current.value = execution("cancel_requested", 2);
        await nextTick();
        assert.equal(second.state.generating.value, true);
        current.value = execution("failed", 3);
        await nextTick();
        assert.equal(second.state.generating.value, false);
        assert.equal(second.state.canSubmit.value, true);
        current.value = execution("cancelled", 4);
        await nextTick();
        assert.equal(second.state.canSubmit.value, true);
      } finally { second.unmount(); }
    });

    await t.test("submission hands execution and exact input to workspace immediately", async () => {
      let accepted;
      const queued = execution("queued");
      const fallback = api(null);
      respond = async (request) => request.path.endsWith("/plant-previews")
        ? { execution: queued, random_seed: 91 } : fallback(request);
      const panel = configuration(ref(null), (run, input) => { accepted = { run, input }; });
      try {
        await panel.state.initialize();
        await panel.state.submitPreview();
        assert.equal(accepted.run.status, "queued");
        assert.equal(accepted.input.random_seed, 91);
        assert.deepEqual(accepted.input.selected_region_ids, ["region-1"]);
        assert.equal(accepted.input.profile_id, "profile");
        assert.equal(panel.state.canSubmit.value, false);
      } finally { panel.unmount(); }
    });

    await t.test("workspace polls without planting panel and restores on page return", async (t) => {
      t.mock.timers.enable({ apis: ["setTimeout"] });
      let current = execution("queued");
      respond = (request) => api(current)(request);
      const first = mount(() => useDtsWorkspace(ref("project")));
      await flush();
      assert.equal(first.state.plantRun.value.status, "queued");
      first.unmount();
      const second = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        assert.equal(second.state.plantRun.value.status, "queued");
        current = execution("running", 2);
        t.mock.timers.tick(2500);
        await flush();
        assert.equal(second.state.plantRun.value.status, "running");
        current = { ...execution("awaiting_confirmation", 3), stage: "review", generated_point_set_id: "points", asset_assignment_set_id: "assignment" };
        t.mock.timers.tick(2500);
        await flush();
        assert.equal(second.state.plantRun.value.status, "awaiting_confirmation");
      } finally { second.unmount(); }
    });

    await t.test("stale refresh cannot clear a newly accepted task", async () => {
      let resolveSummary;
      let stale = true;
      const run = execution();
      const summary = new Promise((resolve) => { resolveSummary = resolve; });
      respond = (request) => request.path.endsWith("/summary") && stale
        ? summary : api(run)(request);
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        workspace.state.acceptPlantPreview(run, previewInput);
        assert.equal(workspace.state.plantRun.value, run);
        stale = false;
        resolveSummary({ plant_run_execution_id: null, scene_preparation_execution_id: null });
        await flush();
        assert.equal(workspace.state.plantRun.value.execution_id, "preview");
        assert.deepEqual(workspace.state.plantInput.value.selected_region_ids, ["region-1"]);
      } finally { workspace.unmount(); }
    });

    await t.test("restores task status before slow input and preparation reads", async () => {
      let releaseInput, releasePreparation;
      const input = new Promise((resolve) => { releaseInput = resolve; });
      const preparation = new Promise((resolve) => { releasePreparation = resolve; });
      const fallback = api(execution());
      respond = (request) => {
        if (request.path.endsWith("/summary")) return {
          plant_run_execution_id: "preview", execution_status: "running",
          scene_preparation_execution_id: "preparation", material_flow_execution_id: null,
        };
        if (request.path.endsWith("/input")) return input;
        if (request.path.endsWith("/scene-preparations/preparation")) return preparation;
        return fallback(request);
      };
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        assert.equal(workspace.state.loading.value, false);
        assert.equal(workspace.state.plantRun.value.status, "running");
        assert.equal(workspace.state.plantInput.value, null);
        releaseInput(previewInput);
        await flush();
        assert.equal(workspace.state.plantInput.value.random_seed, 42);
        assert.equal(workspace.state.preparation.value, null);
        releasePreparation({ status: "succeeded", scene_revision_id: null });
        await flush();
        assert.equal(workspace.state.preparation.value.status, "succeeded");
        assert.equal(workspace.state.plantRun.value.status, "running");
      } finally {
        releaseInput(previewInput);
        releasePreparation({ status: "succeeded", scene_revision_id: null });
        workspace.unmount();
        await flush();
      }
    });

    await t.test("immutable input is cached only for its execution and retried on read failure", async () => {
      let current = execution();
      let reads = 0;
      let fail = true;
      respond = (request) => {
        if (request.path.endsWith("/input")) {
          reads++;
          if (fail) throw new Error("input offline");
          return previewInput;
        }
        if (request.path.endsWith("/plant-previews/next-preview")) return current;
        return api(current)(request);
      };
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        assert.equal(workspace.state.plantRun.value.status, "running");
        assert.match(workspace.state.errorMessage.value, /input offline/);
        assert.equal(reads, 1);
        fail = false;
        await workspace.state.refresh();
        assert.equal(reads, 2);
        const restoredInput = workspace.state.plantInput.value;
        current = execution("running", 2);
        await workspace.state.refresh();
        assert.equal(reads, 2);
        assert.equal(workspace.state.plantInput.value, restoredInput);
        assert.equal(workspace.state.plantRun.value.revision, 2);
        current = { ...execution("queued"), execution_id: "next-preview" };
        await workspace.state.refresh();
        assert.equal(reads, 3);
        assert.equal(workspace.state.plantRun.value.execution_id, "next-preview");
      } finally { workspace.unmount(); }
    });

    await t.test("read failure does not unlock a running task", async () => {
      respond = api(execution());
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        respond = (request) => {
          if (request.path.endsWith("/summary")) throw new Error("offline");
          return api(execution())(request);
        };
        await workspace.state.refresh();
        assert.equal(workspace.state.plantRun.value.status, "running");
        assert.match(workspace.state.errorMessage.value, /offline/);
      } finally { workspace.unmount(); }
    });

    await t.test("cancel sends one request and keeps the task locked until terminal", async () => {
      let current = execution();
      let releaseCancel;
      let cancels = 0;
      respond = async (request) => {
        if (request.path.endsWith("/cancel")) {
          cancels++;
          await new Promise((resolve) => { releaseCancel = resolve; });
          current = execution("cancel_requested", 2);
          return { execution: current, accepted: true, worker_signal_sent: true };
        }
        return api(current)(request);
      };
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        const first = workspace.state.cancelPlantPreview();
        await flush();
        assert.equal(workspace.state.cancellingPlantPreview.value, true);
        await workspace.state.cancelPlantPreview();
        assert.equal(cancels, 1);
        releaseCancel();
        await first;
        await flush();
        assert.equal(workspace.state.plantRun.value.status, "cancel_requested");
        assert.equal(workspace.state.cancellingPlantPreview.value, false);
        await workspace.state.cancelPlantPreview();
        assert.equal(cancels, 1);
        current = execution("cancelled", 3);
        await workspace.state.refresh();
        assert.equal(workspace.state.plantRun.value.status, "cancelled");
      } finally { workspace.unmount(); }
    });

    await t.test("cancel failure preserves execution and allows retry", async () => {
      let current = execution();
      let fail = true;
      respond = (request) => {
        if (request.path.endsWith("/cancel")) {
          if (fail) throw new Error("cancel offline");
          current = execution("cancel_requested", 2);
          return { execution: current, accepted: true, worker_signal_sent: true };
        }
        return api(current)(request);
      };
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        await workspace.state.cancelPlantPreview();
        assert.equal(workspace.state.plantRun.value.status, "running");
        assert.match(workspace.state.plantCancellationError.value, /cancel offline/);
        assert.equal(workspace.state.cancellingPlantPreview.value, false);
        fail = false;
        await workspace.state.cancelPlantPreview();
        assert.equal(workspace.state.plantCancellationError.value, null);
        assert.equal(workspace.state.plantRun.value.status, "cancel_requested");
      } finally { workspace.unmount(); }
    });

    await t.test("accepted cancel stays pending while the owner has not converged", async () => {
      let current = execution();
      let cancels = 0;
      respond = (request) => {
        if (request.path.endsWith("/cancel")) {
          cancels++;
          return { execution: current, accepted: true, worker_signal_sent: true };
        }
        return api(current)(request);
      };
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        await workspace.state.cancelPlantPreview();
        await flush();
        assert.equal(workspace.state.plantRun.value.status, "running");
        assert.equal(workspace.state.cancellingPlantPreview.value, true);
        await workspace.state.refresh();
        await workspace.state.cancelPlantPreview();
        assert.equal(cancels, 1);
        current = execution("cancel_requested", 2);
        await workspace.state.refresh();
        assert.equal(workspace.state.cancellingPlantPreview.value, false);
        assert.equal(workspace.state.plantRun.value.status, "cancel_requested");
        current = execution("cancelled", 3);
        await workspace.state.refresh();
        assert.equal(workspace.state.plantRun.value.status, "cancelled");
      } finally { workspace.unmount(); }
    });

    await t.test("late cancel failure does not mark a resolved task as failed", async () => {
      let current = execution();
      let rejectCancel;
      respond = (request) => request.path.endsWith("/cancel")
        ? new Promise((resolve, reject) => { rejectCancel = reject; }) : api(current)(request);
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        const pending = workspace.state.cancelPlantPreview();
        await flush();
        current = execution("cancelled", 3);
        await workspace.state.refresh();
        rejectCancel(new Error("cancel offline"));
        await pending;
        assert.equal(workspace.state.plantCancellationError.value, null);
        assert.equal(workspace.state.plantRun.value.status, "cancelled");
      } finally { workspace.unmount(); }
    });

    await t.test("late cancellation response cannot overwrite a newer terminal state", async () => {
      let current = execution();
      let releaseCancel;
      respond = (request) => request.path.endsWith("/cancel")
        ? new Promise((resolve) => { releaseCancel = resolve; }) : api(current)(request);
      const workspace = mount(() => useDtsWorkspace(ref("project")));
      try {
        await flush();
        const pending = workspace.state.cancelPlantPreview();
        await flush();
        current = execution("cancelled", 3);
        await workspace.state.refresh();
        releaseCancel({ execution: execution("cancel_requested", 2), accepted: true, worker_signal_sent: true });
        await pending;
        assert.equal(workspace.state.plantRun.value.status, "cancelled");
      } finally { workspace.unmount(); }
    });
  });
  await test("multi-Catalog drafts preserve selections and submit/save portable facts without a fake source", async () => {
    const catalogs = ['permanent', 'temporary'].map(catalog_id => ({
      catalog_id, target_type: 'dts', provider_id: 'dts.static-mesh', display_name: catalog_id,
    }));
    const assets = catalogs.map((source, index) => ({
      asset_id: 'tree_01', display_name: source.catalog_id, thumbnail_resource_id: 'thumb-' + index,
      collection_ids: [], tags: [], provider_metadata: { model_name: 'Model' + index },
      suggested_scale: { mode: 'fixed', value: 1 }, source_catalog_name: source.catalog_id,
      binding_candidate: { asset_id: 'binding-' + source.catalog_id, display_name_snapshot: source.catalog_id,
        weight: 1, scale: { mode: 'fixed', value: 1 }, reference_radius_m: index + 2,
        target_asset_ref: { target_type: 'dts', provider_id: 'dts.static-mesh', locator: { model_name: 'Model' + index } },
        catalog_link: { catalog_id: source.catalog_id, asset_id: 'tree_01', observed_target_hash: 'hash', observed_metadata_hash: 'hash' } },
    }));
    let submitted, saved;
    const fallback = api(null);
    respond = async request => {
      const path = new URL(request.path, 'http://localhost').pathname;
      if (path === '/api/v1/asset-catalogs') return { items: catalogs };
      const match = path.match(/\/asset-catalogs\/dts\/([^/]+)\/assets$/);
      if (match) return { items: [assets[catalogs.findIndex(item => item.catalog_id === match[1])]], collections: [], next_after_ordinal: null };
      if (path.endsWith('/plant-previews') && request.method === 'POST') {
        submitted = JSON.parse(request.body);
        return { execution: execution('queued'), random_seed: 42 };
      }
      if (path === '/api/v1/role-binding-profiles' && request.method === 'POST') {
        saved = JSON.parse(request.body);
        return { summary: { ...profile, profile_id: 'mixed', generation: 1 }, assignments: saved.assignments };
      }
      return fallback(request);
    };
    const view = mount(() => usePlantingConfiguration({
      sessionId: 'session', sceneRevisionId: 'scene', regionSetId: 'regions', selectedRegionIds: () => ['r'],
      currentExecution: () => null, onPreviewSubmitted() {},
    }));
    try {
      await view.state.initialize();
      await view.state.chooseStrategy('strategy');
      const { defaultCandidate: draftCandidate } = await server.ssrLoadModule('/src/features/shared/roleMapping.ts');
      const firstAsset = { ...assets[0], asset_id: assets[0].binding_candidate.asset_id };
      const secondAsset = { ...assets[1], asset_id: assets[1].binding_candidate.asset_id };
      view.state.replaceRoleMappings({ tree: [draftCandidate(firstAsset.asset_id, firstAsset)] });
      view.state.updateCandidate('tree', 'binding-permanent', { fixedScale: 0.8 });
      await view.state.chooseCatalog('temporary');
      assert.equal(view.state.roleMappings.value.tree[0].fixedScale, 0.8);
      view.state.replaceRoleMappings({ tree: [...view.state.roleMappings.value.tree, draftCandidate(secondAsset.asset_id, secondAsset)] });
      assert.equal(view.state.roleMappings.value.tree[0].asset.thumbnail_resource_id, 'thumb-0');
      assert.equal(view.state.currentAssignments.value.tree.candidates[0].reference_radius_m, 2);
      await view.state.chooseCatalog('permanent');
      assert.equal(view.state.roleMappings.value.tree.length, 2);
      assert.equal(view.state.roleMappings.value.tree[0].assetId, 'binding-permanent');
      await view.state.submitPreview();
      assert.equal(submitted.catalog_id, null);
      assert.equal(submitted.profile_id, null);
      assert.equal(submitted.binding_overrides.tree.candidates[1].target_asset_ref.locator.model_name, 'Model1');
      await view.state.saveProfile('Mixed', true);
      assert.equal(saved.catalog_id, null);
      assert.equal(saved.assignments.tree.candidates.length, 2);
      assert.equal(saved.assignments.tree.candidates[0].catalog_link.asset_id, 'tree_01');
      const { serializeMappings, defaultCandidate, hasRepeatedTarget } = await server.ssrLoadModule('/src/features/shared/roleMapping.ts');
      const asset = { ...assets[0], asset_id: 'binding-permanent', suggested_scale: { mode: 'fixed', value: 1.3 } };
      const draft = { ...defaultCandidate(asset.asset_id, asset), scaleMode: 'catalog' };
      assert.equal(serializeMappings({ tree: [draft] }).tree.candidates[0].scale.value, 1.3);
      const duplicate = { ...draft, assetId: 'another-source' };
      assert.equal(hasRepeatedTarget([draft, duplicate]), true);
    } finally { view.unmount(); }
  });
} finally {
  await server.close();
  delete globalThis.window;
  delete globalThis.document;
}
