const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vue = require('vue');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');

function region(id) {
  return { region_id: id, name: 'Area ' + id, bbox: [0, 0, 0, 10, 10, 0],
    coordinates: [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]] };
}

function fixture({ manualYield = false } = {}) {
  const disposed = [], yields = [];
  const calls = { shown: [], hidden: [], removed: [], appearance: [], drawing: 0, cancelled: 0 };
  let onActorPick;
  const sdk = {
    setRegionSelectionAppearance: (...args) => calls.appearance.push(args),
    showRegion: item => calls.shown.push(item.path_id ?? item.region_id),
    hideRegion: id => calls.hidden.push(id),
    removeRegion: id => calls.removed.push(id),
    beginDrawing: () => calls.drawing++,
    cancelDrawing: () => calls.cancelled++,
    startActorPicking: listener => { onActorPick = listener; },
    stopActorPicking: () => { onActorPick = null; },
    async previewActorPick() {},
    releaseActorCapture() {},
    addImportedPage: async items => calls.shown.push(...items.map(item => item.region_id)),
    extractActor: async () => ({ dataset_id: 'actor', region_count: 2 }),
  };
  const api = {
    importRegionsShapefile: async () => ({ imported: true, source_name: 'areas.zip', regions: [region('imported')] }),
    regionGeometryPage: async (dataset, start) => ({ regions: [{
      ...region(start ? 'detached' : 'picked'), dataset_id: dataset, ordinal: start, vertex_count: 4,
    }] }),
  };
  class ExplorerAbortController {
    signal = { aborted: false };
    abort() { this.signal.aborted = true; }
  }
  const load = createModuleLoader({
    document: { getElementById: () => null },
    window: { setTimeout: callback => manualYield ? yields.push(callback) : setTimeout(callback, 0),
      addEventListener() {}, removeEventListener() {} },
    TextEncoder, AbortController: ExplorerAbortController,
  }, {
    vue: { ...vue, onMounted: callback => callback(), onBeforeUnmount: callback => disposed.push(callback) },
    [path.resolve(__dirname, '../src/api.ts')]: api,
  });
  const state = {
    samplingFailures: vue.ref([]), panelCollapsed: vue.ref(false),
    operationActive: vue.ref(false), submitting: vue.ref(false),
    errorMessage: vue.ref(null), sdkReady: vue.ref(true), sdk,
    inputGeometry: vue.ref('polygon'),
  };
  const scope = vue.effectScope();
  const { useRegionDrafts } = load(path.resolve(__dirname, '../src/features/useRegionDrafts.ts'));
  const model = scope.run(() => useRegionDrafts(state));
  return { model, calls, state, sdk, api, yields, pick: input => onActorPick(input),
    dispose() { disposed.forEach(callback => callback()); scope.stop(); } };
}

test('Actor drafts over the inline vertex cap are shown by page and committed as enabled references', async () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('manual')]);
    f.model.regions.value[0].enabled = false;
    const originalPage = f.api.regionGeometryPage;
    const pages = [];
    f.api.regionGeometryPage = async (dataset, start) => {
      pages.push([dataset, start]);
      assert.equal(f.model.regions.value.length, 1);
      const page = await originalPage(dataset, start);
      page.regions[0].holes = [Array(100000).fill([1, 1, 0])];
      page.regions[0].vertex_count = 100004;
      return page;
    };
    const actor = f.model.actorPicking;
    actor.start();
    f.pick({ layer: 'terrain', actor: '47', point: [2, 2, 8] });
    await new Promise(setImmediate);
    assert.equal(f.model.regions.value.length, 1);
    assert.equal(f.model.selectionLocked.value, true);
    assert.equal(actor.canExtract.value, true);
    await actor.extract();
    assert.equal(actor.phase.value, 'idle');
    assert.equal(f.model.selectionLocked.value, false);
    assert.deepEqual(pages, [['actor', 0], ['actor', 1]]);
    assert.deepEqual(f.calls.shown, ['picked', 'detached']);
    assert.deepEqual(f.calls.removed, []);
    assert.deepEqual(Array.from(f.model.regions.value, r => [r.region_id, r.enabled, r.visible]),
      [['manual', false, false], ['picked', true, true], ['detached', true, true]]);
    assert.deepEqual(Array.from(f.model.regions.value.slice(1), r => [r.dataset_id, r.ordinal, r.coordinates.length, r.holes]),
      [['actor', 0, 0, undefined], ['actor', 1, 0, undefined]]);
  } finally { f.dispose(); }
});

test('a missing Actor page rolls back its preview without touching prior drafts', async () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('existing')]);
    const originalPage = f.api.regionGeometryPage;
    f.api.regionGeometryPage = (dataset, start) => start ? Promise.resolve({ regions: [] }) : originalPage(dataset, start);
    f.model.actorPicking.start();
    f.pick({ layer: 'terrain', actor: '47', point: [2, 2, 8] });
    await new Promise(setImmediate);
    await f.model.actorPicking.extract();
    assert.deepEqual(f.calls.shown, ['picked']);
    assert.deepEqual(f.calls.removed, ['picked']);
    assert.deepEqual(Array.from(f.model.regions.value, r => r.region_id), ['existing']);
    assert.equal(f.model.actorPicking.phase.value, 'picking');
    assert.match(f.state.errorMessage.value, /未追加选区/);
  } finally { f.dispose(); }
});

test('cancelling a paged Actor preview after an SDK batch rolls back all staged objects', async () => {
  const f = fixture();
  try {
    let shown;
    f.sdk.addImportedPage = () => new Promise(resolve => { shown = resolve; });
    f.model.actorPicking.start();
    f.pick({ layer: 'terrain', actor: '47', point: [2, 2, 8] });
    await new Promise(setImmediate);
    const task = f.model.actorPicking.extract();
    await new Promise(setImmediate);
    f.model.actorPicking.cancel();
    shown();
    await task;
    assert.deepEqual(f.calls.removed, ['picked']);
    assert.equal(f.model.regions.value.length, 0);
    assert.equal(f.model.actorPicking.phase.value, 'idle');
  } finally { f.dispose(); }
});

test('Completed drawing appends a selected region without changing previous selection or previews', async () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('old-visible'), region('old-hidden')]);
    f.model.regions.value[0].visible = true;
    f.model.regions.value[1].enabled = false;
    f.model.addDrawnRegion(region('new'));
    await vue.nextTick();
    assert.deepEqual(Array.from(f.model.regions.value, item => item.enabled), [true, false, true]);
    assert.deepEqual(Array.from(f.model.regions.value, item => item.visible), [true, false, true]);
    assert.deepEqual(f.calls.hidden, []);
    assert.deepEqual(f.calls.shown, ['new']);
    assert.equal(f.model.enabledRegionCount.value, 2);
    f.model.addDrawnRegion(region('newer'));
    assert.deepEqual(Array.from(f.model.regions.value, item => item.enabled), [true, false, true, true]);
    f.model.selectAllRegions();
    assert.equal(f.model.enabledRegionCount.value, 4);
  } finally { f.dispose(); }
});

test('input kind changes preserve incompatible drafts without selecting, clearing or exporting them', async () => {
  const f = fixture();
  try {
    f.model.addDrawnRegion(region('area'));
    f.state.inputGeometry.value = 'polyline';
    await vue.nextTick();
    assert.equal(f.model.regions.value.length, 0);
    assert.deepEqual(f.calls.hidden, ['area']);
    f.model.addDrawnRegion({path_id:'route',name:'Route',coordinates:[[0,0,0],[10,0,0]],bbox:[0,0,0,10,0,0]});
    assert.equal(f.model.enabledRegionCount.value, 1);
    assert.equal(f.model.regions.value[0].path_id, 'route');
    await f.model.importRegions();
    assert.equal(f.model.regions.value.length, 1);
    f.state.inputGeometry.value = 'polygon';
    await vue.nextTick();
    assert.equal(f.model.regions.value[0].region_id, 'area');
    assert.equal(f.model.regions.value[0].enabled, true);
    assert.deepEqual(f.calls.removed, []);
    f.model.completeRegions();
    assert.equal(f.model.enabledRegionCount.value, 0);
    f.state.inputGeometry.value = 'polyline';
    await vue.nextTick();
    assert.equal(f.model.enabledRegionCount.value, 1);
  } finally { f.dispose(); }
});

test('unchecking a region only dims its preview; hiding it is a separate action', async () => {
  const f = fixture();
  try {
    f.model.addDrawnRegion(region('area'));
    const [area] = f.model.regions.value;
    f.model.setRegionEnabled(area, false);
    assert.equal(area.enabled, false);
    assert.equal(area.visible, true);
    assert.deepEqual(f.calls.appearance, [['area', false]]);
    assert.deepEqual(f.calls.hidden, []);
    await f.model.toggleRegionPreview(area);
    assert.equal(area.visible, false);
    assert.deepEqual(f.calls.hidden, ['area']);
    await f.model.toggleRegionPreview(area);
    assert.equal(area.enabled, false);
    assert.equal(area.visible, true);
    assert.deepEqual(f.calls.appearance, [['area', false], ['area', false]]);
  } finally { f.dispose(); }
});

test('Starting or cancelling a drawing leaves existing selection and visibility intact', () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('old')]);
    f.model.regions.value[0].visible = true;
    f.model.startDrawing();
    f.model.cancelDrawing();
    assert.equal(f.calls.drawing, 1);
    assert.equal(f.calls.cancelled, 1);
    assert.equal(f.model.regions.value[0].enabled, true);
    assert.equal(f.model.regions.value[0].visible, true);
    assert.deepEqual(f.calls.hidden, []);
  } finally { f.dispose(); }
});

test('A failed new preview keeps both the new and previous selections', () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('old')]);
    f.model.regions.value[0].visible = true;
    f.sdk.showRegion = () => { throw new Error('Preview unavailable'); };
    f.model.addDrawnRegion(region('new'));
    assert.deepEqual(Array.from(f.model.regions.value, item => item.enabled), [true, true]);
    assert.deepEqual(Array.from(f.model.regions.value, item => item.visible), [true, false]);
    assert.match(f.state.errorMessage.value, /Preview unavailable/);
  } finally { f.dispose(); }
});

test('Planting completion deselects and hides successful regions while preserving failed and unselected regions', () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('done'), region('failed'), region('unselected')]);
    f.model.regions.value.forEach(item => { item.visible = true; });
    f.model.regions.value[2].enabled = false;
    f.state.samplingFailures.value = [{ region_id: 'failed', name: 'Failed', reason: 'No hit' }];
    f.model.completeRegions();
    assert.deepEqual(Array.from(f.model.regions.value, item => item.enabled), [false, true, false]);
    assert.deepEqual(Array.from(f.model.regions.value, item => item.visible), [false, true, true]);
    assert.deepEqual(f.calls.hidden, ['done']);
    assert.equal(f.model.enabledRegionCount.value, 1);
  } finally { f.dispose(); }
});

test('A preview cleanup failure cannot keep completed regions selected or interrupt other cleanup', () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('a'), region('b')]);
    f.model.regions.value.forEach(item => { item.visible = true; });
    f.sdk.hideRegion = id => { if (id === 'a') throw new Error('Failed'); };
    f.model.completeRegions();
    assert.deepEqual(Array.from(f.model.regions.value, item => item.enabled), [false, false]);
    assert.deepEqual(Array.from(f.model.regions.value, item => item.visible), [true, false]);
    assert.match(f.state.errorMessage.value, /1.*已取消勾选/);
  } finally { f.dispose(); }
});

test('SHP import retains its existing append-and-select behavior', async () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('old')]);
    await f.model.importRegions();
    assert.deepEqual(Array.from(f.model.regions.value, item => item.enabled), [true, true]);
    assert.deepEqual(f.calls.shown, ['imported']);
    assert.equal(f.model.importingRegions.value, false);
  } finally { f.dispose(); }
});

test('paged SHP previews add exact geometry once and retain only references for later show', async () => {
  const f = fixture();
  try {
    const rows = [0, 1].map(i => ({...region(String(i)), dataset_id: 'd', ordinal:i, vertex_count:4}));
    const pages = [];
    f.api.importRegionsShapefile = async () => ({imported:true, dataset_id:'d', region_count:2});
    f.api.regionGeometryPage = async (_, start) => { pages.push(start); return {regions:[rows[start]]}; };
    f.sdk.hasRegion = id => f.calls.shown.includes(id);
    f.sdk.addImportedPage = async rows => rows.forEach(r => f.sdk.showRegion(r));
    await f.model.importRegions();
    assert.deepEqual(pages, [0,1]);
    assert.deepEqual(f.calls.shown, ['0','1']);
    const first = f.model.regions.value[0];
    assert.equal(first.coordinates.length, 0);
    assert.equal(first.vertex_count, 4);
    await f.model.toggleRegionPreview(first);
    await f.model.toggleRegionPreview(first);
    assert.deepEqual(pages, [0,1]);
  } finally { f.dispose(); }
});

test('imported holes are retained and cumulative vertex overflow leaves existing drafts and previews untouched', async () => {
  const f = fixture();
  try {
    const imported = region('holes');
    imported.holes = [[[2,2,0],[3,2,0],[3,3,0],[2,3,0]]];
    f.api.importRegionsShapefile = async () => ({imported:true, regions:[imported]});
    await f.model.importRegions();
    assert.equal(f.model.regions.value[0].holes.length, 1);
    const enormous = region('too-large');
    enormous.holes = [Array.from({length:200000}, () => [1,1,0])];
    f.api.importRegionsShapefile = async () => ({imported:true, regions:[enormous]});
    await f.model.importRegions();
    assert.equal(f.model.regions.value.length, 1);
    assert.deepEqual(f.calls.shown, ['holes']);
    assert.match(f.state.errorMessage.value, /顶点.*200,000.*工作台/);
    assert.equal(f.model.importingRegions.value, false);
  } finally { f.dispose(); }
});

test('clearing or removing drafts deletes native previews, while a visibility toggle only hides them', async () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('a'),region('b')]);
    f.model.regions.value.forEach(item => { item.visible = true; });
    f.model.toggleRegionPreview(f.model.regions.value[0]);
    assert.deepEqual(f.calls.hidden, ['a']);
    assert.deepEqual(f.calls.removed, []);
    f.model.removeRegion(f.model.regions.value[0]);
    assert.deepEqual(f.calls.removed, ['a']);
    await f.model.clearRegions();
    assert.deepEqual(f.calls.removed, ['a','b']);
    assert.equal(f.model.regions.value.length, 0);
  } finally { f.dispose(); }
});

test('The eye action covers all pages and filtered-out regions without changing selection', async () => {
  const f = fixture();
  try {
    f.model.restoreRegions(Array.from({ length: 61 }, (_, i) => region(String(i))));
    f.model.regions.value.forEach((item, i) => { item.enabled = i % 3 === 0; });
    f.model.regions.value[0].visible = true;
    f.model.regions.value[60].visible = true;
    f.model.regionSearch.value = 'Area 1';
    const before = Array.from(f.model.regions.value, item => item.enabled);
    assert.equal(f.model.hasVisibleRegions.value, true);
    await f.model.toggleAllRegionPreviews();
    assert.deepEqual(f.calls.hidden, ['0', '60']);
    assert.equal(f.model.hasVisibleRegions.value, false);
    await f.model.toggleAllRegionPreviews();
    assert.equal(f.calls.shown.length, 61);
    assert.equal(f.model.regions.value.every(item => item.visible), true);
    assert.deepEqual(Array.from(f.model.regions.value, item => item.enabled), before);
    assert.equal(f.model.regionSearch.value, 'Area 1');
  } finally { f.dispose(); }
});

test('Bulk preview errors are isolated and preserve each unsuccessful visibility state', async () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('a'), region('b'), region('c')]);
    f.sdk.showRegion = item => { if (item.region_id === 'b') throw new Error('Failed'); };
    await f.model.toggleAllRegionPreviews();
    assert.deepEqual(Array.from(f.model.regions.value, item => item.visible), [true, false, true]);
    assert.match(f.state.errorMessage.value, /1.*显示/);
    f.sdk.hideRegion = id => { if (id === 'a') throw new Error('Failed'); };
    await f.model.toggleAllRegionPreviews();
    assert.deepEqual(Array.from(f.model.regions.value, item => item.visible), [true, false, false]);
    assert.match(f.state.errorMessage.value, /1.*隐藏/);
    assert.equal(f.model.updatingRegionVisibility.value, false);
    assert.equal(f.model.selectionLocked.value, false);
  } finally { f.dispose(); }
});

test('Bulk visibility obeys SDK, drawing and task locks', async () => {
  const f = fixture();
  try {
    f.model.restoreRegions([region('a')]);
    for (const lock of [f.state.operationActive, f.state.submitting, f.model.drawing, f.model.importingRegions, f.model.exportingRegions, f.model.clearingRegions]) {
      lock.value = true;
      await f.model.toggleAllRegionPreviews();
      lock.value = false;
    }
    f.state.sdkReady.value = false;
    await f.model.toggleAllRegionPreviews();
    assert.deepEqual(f.calls.shown, []);
    assert.deepEqual(f.calls.hidden, []);
  } finally { f.dispose(); }
});

test('Large bulk updates yield, lock re-entry and stop issuing SDK calls after disposal', async () => {
  const f = fixture({ manualYield: true });
  f.model.restoreRegions(Array.from({ length: 45 }, (_, i) => region(String(i))));
  const pending = f.model.toggleAllRegionPreviews();
  assert.equal(f.calls.shown.length, 20);
  assert.equal(f.model.selectionLocked.value, true);
  await f.model.toggleAllRegionPreviews();
  f.model.startDrawing();
  assert.equal(f.calls.drawing, 0);
  assert.equal(f.calls.shown.length, 20);
  f.dispose();
  f.yields.shift()();
  await pending;
  assert.equal(f.calls.shown.length, 20);
  assert.equal(f.model.updatingRegionVisibility.value, false);
});
