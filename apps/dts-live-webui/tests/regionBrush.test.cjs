const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vue = require('vue');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');
const flush = () => new Promise(setImmediate);

function fixture() {
  const handlers = new Map(), calls = [], changed = [];
  const timers = new Map();
  let now = 0, nextTimer = 0;
  const sdk = {
    startRegionBrush(reset) {
      sdk.reset = reset;
      calls.push('arm');
    },
    setRegionBrushPressed(pressed) { calls.push(pressed ? 'begin' : 'end'); },
    async setRegionBrushCapture(capturing) { calls.push(capturing ? 'brush' : 'roam'); },
    async sampleRegionBrushScreen() {},
    stopRegionBrush() { calls.push('disarm'); },
  };
  const window = {
    addEventListener: (event, callback) => handlers.set(event, callback),
    removeEventListener: event => handlers.delete(event),
    setTimeout: (callback, ms) => { timers.set(++nextTimer, { callback, at: now + ms }); return nextTimer; },
    clearTimeout: id => timers.delete(id),
  };
  const target = { setPointerCapture() {},
    hasPointerCapture: () => true, releasePointerCapture() { calls.push('release'); } };
  const document = { elementFromPoint: () => target };
  let dispose;
  const load = createModuleLoader({ window, document, Date: { now: () => now } }, {
    vue: { ...vue, onMounted: cb => cb(), onBeforeUnmount: cb => { dispose = cb; } },
  });
  const { useRegionBrush } = load(path.join(__dirname, '../src/features/useRegionBrush.ts'));
  const scope = vue.effectScope();
  const blocked = vue.ref(false), error = vue.ref(null);
  const brush = scope.run(() => useRegionBrush(sdk, blocked, error, (...args) => changed.push(args)));
  return { brush, sdk, blocked, error, changed, calls, window, document, advance(milliseconds) {
      now += milliseconds;
      for (const [id, timer] of timers) if (timer.at <= now) { timers.delete(id); timer.callback(); }
    },
    emit(type, props = {}) {
      handlers.get(type)?.({ type, target, button:0, buttons:1, clientX:30, clientY:40,
        ...(type.startsWith('pointer') ? { pointerId:42, pointerType:'mouse' } : {}),
        preventDefault() {}, stopPropagation() {}, ...props });
    },
    begin(props = {}) {
      brush.begin({ currentTarget:target, pointerId:42, pointerType:'mouse', button:0, clientX:10, clientY:20,
        preventDefault() {}, stopPropagation() {}, ...props });
    },
    enter(props = {}, kind = 'capture') { brush.enterSwitch({ pointerType:'mouse', buttons:0, ...props }, kind); },
    wheel(deltaY, props = {}) {
      let captured = false;
      brush.switchOnWheel({ deltaY, deltaX:0, preventDefault() { captured = true; }, stopPropagation() {}, ...props });
      return captured;
    },
    dispose() { dispose(); scope.stop(); } };
}

test('releasing a browser stroke keeps the shield ready for the next stroke', async () => {
  const f = fixture();
  try {
    await f.brush.toggle('exclude');
    assert.deepEqual(f.calls, ['arm']);
    f.begin({ button: 3 });
    assert.equal(f.brush.dragging.value, false);
    assert.equal(f.brush.mode.value, 'exclude');
    assert.deepEqual(f.calls, ['arm']);
    const results = [['a', 'a', 'b'], ['b'], ['c'], ['c']];
    f.sdk.sampleRegionBrushScreen = async () => results.shift();
    f.begin();
    assert.equal(f.brush.dragging.value, true);
    f.enter();
    f.advance(1000);
    assert.equal(f.brush.capturing.value, true);
    f.emit('pointermove');
    await flush();
    f.emit('pointerup', { pointerId: 1 });
    assert.equal(f.brush.dragging.value, true);
    f.emit('mouseup');
    await flush();
    assert.equal(f.brush.mode.value, 'exclude');
    assert.equal(f.brush.dragging.value, false);
    assert.equal(f.brush.capturing.value, true);
    f.begin();
    f.emit('pointermove');
    f.emit('pointerup');
    await flush();
    assert.deepEqual(f.changed, [['a', false], ['b', false], ['c', false]]);
    assert.deepEqual(f.calls, ['arm', 'begin', 'release', 'end', 'begin', 'release', 'end']);
    assert.equal(f.error.value, null);
  } finally { f.dispose(); }
});

for (const button of [1, 2]) {
  test(`scene button ${button} switches to roaming without disarming the brush`, () => {
    const f = fixture();
    try {
      f.brush.toggle('exclude');
      f.enter();
      f.begin({ button });
      f.advance(120);
      assert.equal(f.brush.mode.value, 'exclude');
      assert.equal(f.brush.capturing.value, false);
      assert.equal(f.brush.dragging.value, false);
      f.begin({ button });
      assert.deepEqual(f.calls, ['arm', 'roam']);
      f.enter();
      f.advance(120);
      assert.equal(f.brush.capturing.value, true);
      assert.deepEqual(f.calls, ['arm', 'roam', 'brush']);
    } finally { f.dispose(); }
  });
}

test('pressing right during a stroke releases the pointer and ignores its pending sample', async () => {
  const f = fixture();
  try {
    let resolve;
    f.sdk.sampleRegionBrushScreen = () => new Promise(done => { resolve = done; });
    f.brush.toggle('include');
    f.begin();
    f.emit('pointermove');
    f.brush.roamOnMouseDown({ button: 2, preventDefault() {}, stopPropagation() {} });
    resolve(['late']);
    await flush();
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.capturing.value, false);
    assert.equal(f.brush.dragging.value, false);
    assert.deepEqual(f.changed, []);
    assert.deepEqual(f.calls, ['arm', 'begin', 'release', 'end', 'roam']);
  } finally { f.dispose(); }
});

test('the brush requests at most one footprint at a time and discards a result after switching to roam', async () => {
  const f = fixture();
  try {
    const query = [];
    let resolve;
    f.sdk.sampleRegionBrushScreen = (x, y) => {
      query.push([x,y]);
      return new Promise(done => { resolve = done; });
    };
    await f.brush.toggle('include');
    f.begin();
    assert.deepEqual(query, [[10,20]]);
    resolve(['initial']);
    await flush();
    f.emit('pointermove', {clientX:30,clientY:40});
    assert.deepEqual(query, [[10,20]]);
    f.advance(130);
    f.emit('pointermove', {clientX:30,clientY:40});
    f.advance(100);
    f.emit('pointermove', {clientX:35,clientY:45});
    assert.deepEqual(query, [[10,20], [30,40]]);
    resolve(['middle']);
    await flush();
    f.emit('pointermove', {clientX:35,clientY:45});
    f.emit('pointerup', {clientX:35,clientY:45});
    f.enter();
    f.advance(120);
    resolve(['after-switch']);
    await flush();
    assert.deepEqual(f.changed, [['initial', true], ['middle', true]]);
  } finally { f.dispose(); }
});

test('a fast first stroke resolves its last point even when released before the SDK reply', async () => {
  const f = fixture();
  try {
    const queries = [];
    f.sdk.sampleRegionBrushScreen = (x,y) => new Promise(resolve => queries.push({ x, y, resolve }));
    await f.brush.toggle('include');
    f.begin();
    assert.deepEqual(queries.map(q => [q.x,q.y]), [[10,20]]);
    f.emit('pointermove');
    f.emit('pointerup');
    f.emit('pointermove', {buttons:0, clientX:60,clientY:70});
    assert.equal(f.brush.dragging.value, false);
    assert.equal(f.error.value, null);
    assert.equal(queries.length, 1);
    queries[0].resolve(['start']);
    await flush();
    assert.deepEqual(queries.map(q => [q.x,q.y]), [[10,20], [30,40]]);
    queries[1].resolve(['end']);
    await flush();
    assert.deepEqual(f.changed, [['start', true], ['end', true]]);
    assert.deepEqual(f.calls, ['arm','begin','release','end']);
  } finally { f.dispose(); }
});

test('a tap without a drag has no selection side effects or stale result on the next stroke', async () => {
  const f = fixture();
  try {
    let resolve;
    f.sdk.sampleRegionBrushScreen = () => new Promise(done => { resolve = done; });
    await f.brush.toggle('include');
    f.begin();
    const first = resolve;
    f.emit('pointerup', {clientX:10,clientY:20});
    f.begin();
    first(['old']);
    await flush();
    assert.deepEqual(f.changed, []);
  } finally { f.dispose(); }
});

test('brush explains missing native samples without making selection changes', async () => {
  const f = fixture();
  try {
    await f.brush.toggle('include');
    f.begin();
    f.emit('pointermove');
    f.emit('pointerup');
    await flush();
    assert.match(f.error.value, /未返回场景鼠标坐标/);
    assert.deepEqual(f.changed, []);
    assert.equal(f.brush.mode.value, 'include');
  } finally { f.dispose(); }
});

test('disabling the focused brush button does not cancel the first stroke', async () => {
  const f = fixture();
  try {
    await f.brush.toggle('exclude');
    f.sdk.sampleRegionBrushScreen = async () => ['first'];
    f.begin();
    f.emit('blur', { target: { tagName: 'BUTTON', disabled: true } });
    assert.equal(f.brush.dragging.value, true);
    f.emit('pointermove');
    f.emit('pointerup');
    await flush();
    assert.deepEqual(f.changed, [['first', false]]);
    assert.equal(f.brush.dragging.value, false);
    assert.equal(f.brush.capturing.value, true);
  } finally { f.dispose(); }
});

test('window blur ends only the stroke and a workflow lock disarms the brush', async () => {
  const f = fixture();
  try {
    await f.brush.toggle('include');
    f.begin();
    f.emit('blur', { target: f.window });
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.capturing.value, true);
    assert.deepEqual(f.calls.slice(-3), ['begin', 'release', 'end']);
    f.begin();
    f.blocked.value = true;
    assert.equal(f.brush.mode.value, null);
    assert.deepEqual(f.calls.slice(-4), ['begin', 'release', 'end', 'disarm']);
  } finally { f.dispose(); }
});

test('hover toggles capture once per entry and remembers the brush direction in roam', async () => {
  const f = fixture();
  try {
    await f.brush.toggle('exclude');
    f.enter({ buttons: 2 });
    f.advance(500);
    assert.equal(f.brush.capturing.value, true);
    f.enter();
    f.advance(60);
    f.brush.leaveSwitch();
    f.advance(1000);
    assert.equal(f.brush.capturing.value, true);
    f.enter();
    f.advance(120);
    f.enter();
    f.advance(1000);
    assert.equal(f.brush.capturing.value, false);
    assert.equal(f.brush.mode.value, 'exclude');
    f.begin();
    assert.equal(f.brush.dragging.value, false);
    f.brush.leaveSwitch();
    f.enter();
    f.advance(120);
    assert.equal(f.brush.capturing.value, true);
    assert.deepEqual(f.calls, ['arm', 'roam', 'brush']);
  } finally { f.dispose(); }
});

test('direction hover changes mode once per entry and is inactive during roaming', () => {
  const f = fixture();
  try {
    f.brush.toggle('include');
    f.enter({}, 'direction');
    f.advance(120);
    assert.equal(f.brush.mode.value, 'exclude');
    assert.equal(f.brush.capturing.value, true);
    f.enter({}, 'direction');
    f.advance(1000);
    assert.equal(f.brush.mode.value, 'exclude');
    f.brush.leaveSwitch('direction');
    f.enter();
    f.advance(120);
    f.brush.leaveSwitch('capture');
    f.enter({}, 'direction');
    f.advance(120);
    f.brush.keyboardSwitch({ detail:0 }, 'direction');
    assert.equal(f.brush.mode.value, 'exclude');
    assert.equal(f.brush.capturing.value, false);
    assert.deepEqual(f.calls, ['arm', 'roam']);
  } finally { f.dispose(); }
});

test('editing maps wheel directions to brush modes and only announces actual changes', () => {
  const f = fixture();
  try {
    assert.equal(f.wheel(100), false);
    f.brush.toggle('include');
    assert.equal(f.wheel(100), true);
    assert.equal(f.brush.mode.value, 'exclude');
    assert.equal(f.brush.wheelMode.value, 'exclude');
    f.advance(500);
    f.wheel(100);
    f.advance(500);
    assert.equal(f.brush.wheelMode.value, null);
    f.wheel(-100, { deltaX:150 });
    f.wheel(-100, { ctrlKey:true });
    assert.equal(f.brush.mode.value, 'exclude');
    f.wheel(-100);
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.wheelMode.value, 'include');
    f.begin({ button:2 });
    assert.equal(f.brush.wheelMode.value, null);
    assert.equal(f.wheel(100), false);
    assert.equal(f.brush.mode.value, 'include');
    assert.deepEqual(f.calls, ['arm', 'roam']);
  } finally { f.dispose(); }
});

test('wheel ignores an active stroke and preserves its direction for samples returned after release', async () => {
  const f = fixture();
  try {
    const pending = [];
    f.sdk.sampleRegionBrushScreen = () => new Promise(resolve => pending.push(resolve));
    f.brush.toggle('include');
    f.begin();
    f.emit('pointermove');
    f.wheel(100);
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.wheelMode.value, null);
    f.emit('pointerup');
    f.wheel(100);
    assert.equal(f.brush.mode.value, 'exclude');
    pending.shift()(['start']);
    await flush();
    pending.shift()(['end']);
    await flush();
    assert.deepEqual(f.changed, [['start', true], ['end', true]]);
  } finally { f.dispose(); }
});

test('the cursor follows the scene and dragging over a panel does not paint through it', async () => {
  const f = fixture();
  try {
    const cursor = f.brush.cursor.value = { hidden:true, style:{} };
    f.brush.toggle('include');
    f.brush.hoverScene({ pointerType:'mouse', buttons:0, clientX:10, clientY:20 });
    assert.equal(cursor.hidden, false);
    assert.match(cursor.style.transform, /10px, 20px/);
    const queries = [];
    f.sdk.sampleRegionBrushScreen = async (x,y) => { queries.push([x,y]); return ['first']; };
    f.begin();
    f.emit('pointermove');
    f.document.elementFromPoint = () => ({ tagName:'MAIN' });
    f.emit('pointermove', {clientX:80, clientY:90});
    assert.equal(cursor.hidden, true);
    f.emit('pointerup', {clientX:80, clientY:90});
    await flush();
    assert.deepEqual(queries, [[10,20], [30,40]]);
    assert.deepEqual(f.changed, [['first', true]]);
  } finally { f.dispose(); }
});

test('closing brush mode cancels a pending corner hover', async () => {
  const f = fixture();
  try {
    await f.brush.toggle('include');
    f.enter();
    await f.brush.toggle('include');
    f.advance(1000);
    assert.deepEqual(f.calls, ['arm', 'disarm']);
    assert.equal(f.brush.capturing.value, false);
  } finally { f.dispose(); }
});

test('spatial lookup chooses the newest matching face but can skip hidden faces or holes', () => {
  const load = createModuleLoader({});
  const { RegionHitIndex } = load(path.join(__dirname, '../src/sdk/regionHitIndex.ts'));
  const boxes = Array.from({ length: 5000 }, (_, i) => ({ id:String(i), order:i, bounds:[i * 2, 0, i * 2 + 1, 1] }));
  boxes.push({ id:'overlay', order:5000, bounds:[100, 0, 101, 1] });
  const index = new RegionHitIndex(boxes);
  assert.equal(index.pick(100.5, 0.5, () => true), 'overlay');
  assert.equal(index.pick(100.5, 0.5, id => id !== 'overlay'), '50');
  assert.equal(index.pick(100.5, 2, () => assert.fail('outside candidate bounds')), undefined);
});
