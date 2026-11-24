const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vue = require('vue');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');

const { actorPick, ensureActorPickActive } = createModuleLoader({})(path.join(__dirname, '../src/sdk/actorPicking.ts'));
const pick = (x, y, actor = '47', layer = 'terrain') => ({ layer, actor, point: [x, y, 8] });
const flush = () => new Promise(setImmediate);

test('reads a scene click without using the TileLayer id as the Actor id', () => {
  const p = actorPick({ Type: 'TileLayer', ID: 'terrain', ObjectID: 47, MouseClickPoint: [0, 2, 8] });
  assert.equal(JSON.stringify(p), JSON.stringify(pick(0, 2)));
  assert.equal(actorPick({ Type: 'TileLayer', ID: 'terrain', MouseClickPoint: [0, 2, 8] }), null);
  assert.equal(actorPick({ Type: 'Polygon', ID: 'overlay', ObjectID: 47, MouseClickPoint: [0, 2, 8] }), null);
});

function fixture() {
  const load = createModuleLoader({ AbortController }, {
    vue: { ...vue, onMounted() {}, onBeforeUnmount() {} },
  });
  const { useActorPicking } = load(path.join(__dirname, '../src/features/useActorPicking.ts'));
  const scope = vue.effectScope();
  const calls = [];
  let onPick, onRange;
  const sdk = {
    startActorPicking(listener) { onPick = listener; },
    stopActorPicking() { onPick = undefined; },
    async previewActorPick(target) { calls.push(['highlight', target?.actor ?? null]); },
    beginActorRangeDrawing(finish) { onRange = finish; calls.push(['draw']); },
    cancelActorRangeDrawing() { onRange = undefined; calls.push(['cancel-draw']); },
    showActorRange(region) { calls.push(['show-range', region.region_id]); },
    async clearActorRangePreview() { calls.push(['clear-range']); },
    async extractActor(target, _signal, clip) {
      calls.push(clip ? ['extract', target.actor, clip] : ['extract', target.actor]);
      return { dataset_id:'actor', region_count:2 };
    },
  };
  const blocked = vue.ref(false), error = vue.ref(null);
  const picker = scope.run(() => useActorPicking(sdk, blocked, error,
    async (dataset, count) => { calls.push(['append', dataset, count]); }));
  return { picker, calls, sdk, blocked, error, click: (pick) => onPick?.(pick),
    finish: (region) => onRange?.(region), dispose: () => scope.stop() };
}

test('clicks replace the selected Actor and one confirmation adds all returned regions', async () => {
  const f = fixture();
  try {
    f.picker.start();
    f.click(pick(0, 2));
    assert.equal(f.picker.canExtract.value, false);
    await flush();
    f.click(pick(10, 2));
    f.click(pick(12, 5, '48'));
    await flush();
    assert.equal(f.picker.selected.value.actor, '48');
    assert.equal(f.picker.canExtract.value, true);
    await f.picker.extract();
    assert.deepEqual(f.calls, [['highlight','47'], ['highlight','48'], ['extract','48'],
      ['append','actor',2], ['highlight',null]]);
    assert.equal(f.picker.phase.value, 'idle');
  } finally { f.dispose(); }
});

test('cancelling before registration completes skips loading the result', async () => {
  const f = fixture();
  try {
    let registered;
    f.sdk.extractActor = () => new Promise(resolve => { registered = resolve; });
    f.picker.start();
    f.click(pick(0, 2));
    await flush();
    const task = f.picker.extract();
    await flush();
    f.picker.cancel();
    registered({ dataset_id:'actor', region_count:2 });
    await task;
    assert.deepEqual(f.calls, [['highlight','47'], ['highlight',null]]);
    assert.equal(f.picker.phase.value, 'idle');
  } finally { f.dispose(); }
});

test('range drawing keeps the chosen Actor and only its region is sent as an optional clip', async () => {
  const f = fixture();
  const range = { region_id:'guide', coordinates:[[0,0,8], [10,0,8], [0,10,8]] };
  try {
    f.picker.start();
    f.click(pick(0,2));
    await flush();
    f.picker.drawRange();
    assert.equal(f.picker.phase.value, 'drawing-range');
    assert.equal(f.picker.canExtract.value, false);
    f.click(pick(12,5,'48'));
    f.finish(range);
    f.picker.drawRange();
    f.picker.cancelRangeDrawing();
    assert.equal(f.picker.range.value, range);
    await f.picker.extract();
    assert.deepEqual(f.calls, [['highlight','47'], ['draw'], ['show-range','guide'], ['draw'], ['cancel-draw'],
      ['extract','47',range.coordinates], ['append','actor',2], ['clear-range'], ['highlight',null]]);
    assert.equal(f.picker.range.value, null);
  } finally { f.dispose(); }
});

test('clearing a drawn range restores the whole-Actor request', async () => {
  const f = fixture();
  try {
    f.picker.start();
    f.click(pick(0,2));
    await flush();
    f.picker.drawRange();
    f.finish({ region_id:'guide', coordinates:[[0,0,8], [10,0,8], [0,10,8]] });
    f.picker.clearRange();
    await f.picker.extract();
    assert.deepEqual(f.calls.slice(-4), [['clear-range'], ['extract','47'], ['append','actor',2], ['highlight',null]]);
  } finally { f.dispose(); }
});

test('legacy embedded AbortSignal requires no throwIfAborted method', () => {
  const signal = { aborted: false };
  assert.doesNotThrow(() => ensureActorPickActive(signal));
  signal.aborted = true;
  assert.throws(() => ensureActorPickActive(signal), /拾取已取消/);
});
