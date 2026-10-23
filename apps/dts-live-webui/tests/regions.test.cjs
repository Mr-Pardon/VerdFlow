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
  const calls = { shown: [], hidden: [], drawing: 0, cancelled: 0 };
  const sdk = {
    showRegion: item => calls.shown.push(item.region_id),
    hideRegion: id => calls.hidden.push(id),
    beginDrawing: () => calls.drawing++,
    cancelDrawing: () => calls.cancelled++,
  };
  const api = { importRegionsShapefile: async () => ({ imported: true, source_name: 'areas.zip', regions: [region('imported')] }) };
  const load = createModuleLoader({
    document: { getElementById: () => null },
    window: { setTimeout: callback => manualYield ? yields.push(callback) : setTimeout(callback, 0) },
    TextEncoder,
  }, {
    vue: { ...vue, onBeforeUnmount: callback => disposed.push(callback) },
    [path.resolve(__dirname, '../src/api.ts')]: api,
  });
  const state = {
    samplingFailures: vue.ref([]), panelCollapsed: vue.ref(false),
    operationActive: vue.ref(false), submitting: vue.ref(false),
    errorMessage: vue.ref(null), sdkReady: vue.ref(true), sdk,
  };
  const scope = vue.effectScope();
  const { useRegionDrafts } = load(path.resolve(__dirname, '../src/features/useRegionDrafts.ts'));
  const model = scope.run(() => useRegionDrafts(state));
  return { model, calls, state, sdk, api, yields, dispose() { disposed.forEach(callback => callback()); scope.stop(); } };
}

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
