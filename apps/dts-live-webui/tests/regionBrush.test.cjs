const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vue = require('vue');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');
const flush = () => new Promise(setImmediate);

function fixture(waitForHost) {
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
  const load = createModuleLoader({ window, document, AbortController, Date: { now: () => now } }, {
    vue: { ...vue, onMounted: cb => cb(), onBeforeUnmount: cb => { dispose = cb; } },
  });
  const { useRegionBrush } = load(path.join(__dirname, '../src/features/useRegionBrush.ts'));
  const scope = vue.effectScope();
  const blocked = vue.ref(false), error = vue.ref(null);
  const brush = scope.run(() => useRegionBrush(sdk, blocked, error, (...args) => changed.push(args), () => true, waitForHost));
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
      let prevented = false;
      const event = { currentTarget:target, pointerId:42, pointerType:'mouse', button:0, clientX:10, clientY:20,
        preventDefault() { prevented = true; }, stopPropagation() {}, ...props };
      brush.begin(event);
      if (!prevented) brush.auxiliaryDown(event);
    },
    enter(props = {}, kind = 'capture') { brush.enterSwitch({ pointerType:'mouse', buttons:0, ...props }, kind); },
    wheel(deltaY, props = {}) {
      let captured = false;
      brush.resizeOnWheel({ deltaY, deltaX:0, preventDefault() { captured = true; }, stopPropagation() {}, ...props });
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

test('middle switches direction once per click; right returns to native roam', () => {
  const f = fixture();
  try {
    f.brush.toggle('include');
    f.begin({ button:1 });
    assert.equal(f.brush.mode.value, 'exclude');
    assert.equal(f.brush.directionNotice.value, 'exclude');
    assert.equal(f.brush.capturing.value, true);
    f.begin({ button:1 });
    assert.equal(f.brush.mode.value, 'include');
    f.enter();
    f.begin({ button:2 });
    f.advance(120);
    f.begin({ button:1 });
    assert.equal(f.brush.capturing.value, false);
    assert.equal(f.brush.mode.value, 'include');
    assert.deepEqual(f.calls, ['arm', 'roam']);
  } finally { f.dispose(); }
});

test('returning via the hotzone requests focus only after mount and never in roam or after exit', async () => {
  const f = fixture();
  const focus = [];
  f.window.focus = () => focus.push('window');
  f.document.hasFocus = () => false;
  try {
    f.brush.toggle('include');
    f.begin({ button:2 });
    await vue.nextTick();
    assert.deepEqual(focus, []);
    f.enter();
    f.advance(120);
    assert.deepEqual(focus, []);
    f.brush.shield.value = { focus: opts => focus.push(opts.preventScroll) };
    await vue.nextTick();
    f.enter();
    f.advance(1000);
    await vue.nextTick();
    assert.deepEqual(focus, ['window', true]);
    f.begin({ button:2 });
    f.enter();
    f.advance(120);
    f.brush.stop();
    await vue.nextTick();
    assert.deepEqual(focus, ['window', true]);
  } finally { f.dispose(); }
});

test('pressing right during a stroke releases the pointer and ignores its pending sample', async () => {
  const f = fixture();
  try {
    let resolve;
    f.sdk.sampleRegionBrushScreen = () => new Promise(done => { resolve = done; });
    f.brush.toggle('include');
    f.begin();
    f.emit('pointermove');
    f.brush.auxiliaryDown({ button: 2, preventDefault() {}, stopPropagation() {} });
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

test('wheel resizes continuously in edit only; zoom, direction and shortcuts stay independent', () => {
  const f = fixture();
  try {
    assert.equal(f.wheel(100), false);
    f.brush.toggle('include');
    const r = f.brush.radius;
    assert.equal(r.value, 18);
    assert.equal(f.wheel(-0.25), true);
    assert.ok(r.value > 18 && r.value < 19);
    f.wheel(-1, { deltaMode:1 });
    f.wheel(16.25);
    assert.ok(Math.abs(r.value - 18) < 1e-8);
    const before = r.value;
    f.wheel(-100, { deltaX:150 });
    f.emit('keydown', { key:'Control' });
    f.wheel(-100, { ctrlKey:true });
    f.emit('keyup', { key:'Control' });
    assert.equal(r.value, before);
    f.wheel(-100000);
    assert.equal(r.value, 64);
    f.wheel(100000);
    assert.equal(r.value, 4);
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.directionNotice.value, null);
    f.begin({ button:2 });
    assert.equal(f.brush.directionNotice.value, null);
    assert.equal(f.wheel(100), false);
    assert.equal(r.value, 4);
    assert.equal(f.brush.mode.value, 'include');
    assert.deepEqual(f.calls, ['arm', 'roam']);
  } finally { f.dispose(); }
});

test('a released stroke finishes at its original radius even if the next brush was resized', async () => {
  const f = fixture();
  try {
    const pending = [];
    const radii = [];
    f.sdk.sampleRegionBrushScreen = (x, y, radius) => {
      radii.push(radius);
      return new Promise(resolve => pending.push(resolve));
    };
    f.brush.toggle('include');
    f.begin();
    f.emit('pointermove');
    f.wheel(100);
    assert.equal(f.brush.radius.value, 18);
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.directionNotice.value, null);
    f.emit('pointerup');
    f.wheel(100);
    assert.ok(f.brush.radius.value < 18);
    pending.shift()(['start']);
    await flush();
    pending.shift()(['end']);
    await flush();
    assert.deepEqual(f.changed, [['start', true], ['end', true]]);
    assert.deepEqual(radii, [18, 18]);
    f.begin();
    assert.equal(radii[2], f.brush.radius.value);
  } finally { f.dispose(); }
});

test('only Shift and Ctrl choose brush direction in edit; Tab is not a brush shortcut', () => {
  const f = fixture();
  const tap = key => { f.emit('keydown', { key }); f.emit('keyup', { key }); };
  try {
    tap('Tab');
    assert.equal(f.brush.mode.value, null);
    f.brush.toggle('exclude');
    tap('Shift');
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.directionNotice.value, 'include');
    tap('Tab');
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.capturing.value, true);
    tap('Control');
    assert.equal(f.brush.mode.value, 'exclude');
    assert.equal(f.brush.directionNotice.value, 'exclude');
    assert.equal(f.brush.capturing.value, true);
    f.begin({ button:2 });
    assert.equal(f.brush.capturing.value, false);
    assert.equal(f.brush.directionNotice.value, null);
    tap('Shift');
    assert.equal(f.brush.mode.value, 'exclude');
    tap('Tab');
    assert.equal(f.brush.capturing.value, false);
    assert.equal(f.brush.mode.value, 'exclude');
    assert.deepEqual(f.calls, ['arm', 'roam']);
  } finally { f.dispose(); }
});

test('native scene tap can return from roam and choose a direction without any pointer event', async () => {
  const waits = [];
  const f = fixture((mode, signal) => new Promise(resolve => waits.push({ mode, signal, resolve })));
  try {
    f.brush.toggle('include');
    f.begin({ button:2 });
    assert.equal(waits[0].signal.aborted, true);
    assert.equal(waits.at(-1).mode, 'roam');
    waits.at(-1).resolve({ available:true, action:'edit' });
    await flush();
    assert.equal(f.brush.capturing.value, true);
    assert.equal(waits.at(-1).mode, 'edit');
    for (const direction of ['exclude', 'include']) {
      waits.at(-1).resolve({ available:true, action:direction });
      await flush();
      assert.equal(f.brush.mode.value, direction);
    }
    const pending = waits.at(-1);
    f.emit('keydown', { key:'Alt', altKey:true });
    assert.equal(pending.signal.aborted, true);
    f.emit('keyup', { key:'Alt' });
    pending.resolve({ available:true, action:'edit' });
    await flush();
    assert.equal(f.brush.capturing.value, false);
    assert.deepEqual(f.calls, ['arm', 'roam', 'brush', 'roam']);
    f.brush.stop();
    assert.equal(waits.at(-1).signal.aborted, true);
  } finally { f.dispose(); }
});

test('Alt switches on release only in the armed scene; Tab remains native', () => {
  const f = fixture();
  let consumed = 0;
  const tap = (key, target = {}) => {
    for (const type of ['keydown','keyup']) f.emit(type, { key, altKey:type === 'keydown' && key === 'Alt', target, preventDefault:() => consumed++ });
  };
  try {
    tap('Alt');
    assert.equal(consumed, 0);
    f.brush.toggle('include');
    const panel = { closest:() => true };
    f.emit('focusin', { target:panel });
    tap('Alt', panel);
    tap('Tab', panel);
    assert.equal(consumed, 0);
    assert.equal(f.brush.capturing.value, true);
    f.emit('focusout', { relatedTarget:null });
    tap('Tab'); tap('Alt'); tap('Alt');
    assert.equal(consumed, 2);
    assert.deepEqual(f.calls, ['arm', 'roam', 'brush']);
  } finally { f.dispose(); }
});

test('brush shortcuts ignore combined keys, edit fields and interrupted taps', () => {
  const f = fixture();
  try {
    f.brush.toggle('include');
    let captured = false;
    const preventDefault = () => { captured = true; };
    for (const [modifier, key] of [['Control', 'c'], ['Shift', 'Tab'], ['Alt', 'Tab']]) {
      f.emit('keydown', { key: modifier, preventDefault });
      f.emit('keydown', { key, preventDefault });
      f.emit('keyup', { key });
      f.emit('keyup', { key: modifier, preventDefault });
    }
    const target = { closest: () => true };
    f.emit('keydown', { key:'Control', target });
    f.emit('keyup', { key:'Control', target, preventDefault });
    f.emit('keydown', { key:'Control' });
    f.emit('pointerdown');
    f.emit('keyup', { key:'Control', preventDefault });
    f.begin();
    f.emit('keydown', { key:'Control' });
    f.emit('keyup', { key:'Control', preventDefault });
    f.emit('pointerup');
    f.emit('keydown', { key:'Control' });
    f.emit('blur', { target:f.window });
    f.emit('keyup', { key:'Control', preventDefault });
    assert.equal(captured, false);
    assert.equal(f.brush.mode.value, 'include');
    assert.equal(f.brush.capturing.value, true);
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

test('packed brush index agrees with scanning mixed, coincident and elongated bounds', () => {
  const load = createModuleLoader({});
  const { RegionHitIndex } = load(path.join(__dirname, '../src/sdk/regionHitIndex.ts'));
  const boxes = Array.from({length:2050}, (_, i) => {
    const x = i % 5 ? (i * 13) % 89 : 0, y = i % 5 ? (i * 7) % 73 : 0;
    return { id:String(i), order:i, bounds:[x, y, x + i % 41, y + i % 17] };
  });
  const index = new RegionHitIndex([...boxes]);
  for (let x = -2; x < 128; x += 7) {
    const y = (x * 3) % 82, bounds = [x,y,x+18,y+3];
    const overlap = (b, w, h) => b[0] <= x+w && b[2] >= x && b[1] <= y+h && b[3] >= y;
    const visible = id => Number(id) % 3 !== 0;
    assert.deepEqual(new Set(index.intersect(bounds, visible)), new Set(boxes.filter(b => visible(b.id) && overlap(b.bounds,18,3)).map(b => b.id)));
    assert.equal(index.pick(x,y,visible), boxes.findLast(b => visible(b.id) && overlap(b.bounds,0,0))?.id);
  }
});
