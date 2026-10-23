const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { webcrypto } = require('node:crypto');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');

async function fixture({ cef = true, miss = false, timeout = false, containsPoint = false, transformResponse } = {}) {
  const calls = { pickMasks: [], camera: [], cameraReads: [], restoredCamera: [], sequence: [], lines: [], polygons: [], deleted: [], elevation: [], chunks: [], clicked: [], delivery: [], regions: [], errors: [], activity: [] };
  let events;
  let instance;
  const window = {
    ue: { internal: {} },
    setTimeout: (callback, ms) => setTimeout(callback, ms === 1400 || ms === 800 ? 5 : timeout && ms === 30000 ? 10 : ms),
    clearTimeout,
  };
  window.DigitalTwinAPI = class {
    isCalledInCEF = cef;
    callbackIndex = 0;
    constructor(_, callbacks) { events = callbacks; instance = this; queueMicrotask(callbacks.onReady); }
    settings = { setMousePickMask: mask => { calls.pickMasks.push(mask); } };
    onConnectionMessage = raw => {
      const event = JSON.parse(raw);
      if (event.eventtype === 'LeftMouseButtonClick' && !(calls.pickMasks.at(-1) & 1)) return;
      events.onEvent(event);
    };
    editHelper = { setParam() {}, start() {}, cancel() {} };
    camera = {
      lookAtBBox: (...args) => { calls.sequence.push('focus'); calls.camera.push(args); },
      get: callback => {
        calls.sequence.push('capture');
        const response = { callbackIndex: ++this.callbackIndex, camera: [120, 230, 340, -45, 60, 0] };
        calls.cameraReads.push(response);
        queueMicrotask(() => cef ? window.tick_next(response) : callback(response));
      },
      set: (...args) => { calls.sequence.push('restore'); calls.restoredCamera.push(args); },
    };
    polygon = { add: data => calls.polygons.push(data), delete: id => calls.deleted.push(id) };
    tools = { linesIntersect: (lines, precision, details, callback) => {
      calls.lines.push(lines);
      const index = ++this.callbackIndex;
      const hits = lines.map(line => (typeof miss === 'function' ? miss(line) : miss) ? [] : [{ LineIntersectPoint: [line.start[0], line.start[1], 12] }]);
      const raw = lines.length === 1
        ? { callbackIndex:index, result:0, resultMessage:'OK', LineIntersectPoint:hits[0][0]?.LineIntersectPoint ?? null }
        : { callbackIndex:index, intersects:hits };
      const response = transformResponse ? transformResponse(raw) : raw;
      if (!timeout) queueMicrotask(() => cef ? window.tick_next(response) : callback(response));
    } };
    call = request => { queueMicrotask(() => window.tick_next({ eventtype: 'ImportXMLObject', path: request.data[0].path, resultMessage: 'Succeed' })); };
  };
  const document = {
    write() {}, writeln() {},
    createElement: name => name === 'canvas' ? { getContext: () => ({ isPointInPath: () => containsPoint }) } : {},
    head: { appendChild: script => queueMicrotask(() => script.onload()) },
  };
  const api = {
    cancelOperation: async () => ({}), submitSelection: async () => ({}),
    submitElevation: async (...args) => calls.elevation.push(args),
    submitElevationChunk: async (...args) => calls.chunks.push(args),
    submitDelivery: async (...args) => calls.delivery.push(args),
  };
  class Path2D { moveTo() {} lineTo() {} closePath() {} }
  const load = createModuleLoader({ window, document, Path2D, crypto: webcrypto, console }, {
    [path.resolve(__dirname, '../src/api.ts')]: api,
  });
  const { DtsSdkBridge } = load(path.join(__dirname, '../src/sdk/DtsSdkBridge.ts'));
  const { createLiveRegion } = load(path.join(__dirname, '../src/sdk/regions.ts'));
  const { samplingWindows } = load(path.join(__dirname, '../src/sdk/elevation.ts'));
  const bridge = new DtsSdkBridge({
    onReady() {}, onActivity: message => calls.activity.push(message), onError: message => calls.errors.push(message),
    onCommandComplete() {}, onRegionDrawn: region => calls.regions.push(region), onDrawingFinished() {},
    onRegionClicked: id => calls.clicked.push(id),
  });
  await bridge.initialize();
  return { bridge, calls, events, window, document, instance, createLiveRegion, samplingWindows, api };
}

function command() {
  return {
    sequence: 1, kind: 'sample_elevation', correlation: { connector_session_id: 's', operation_id: 'op', generation_token: 1, request_id: 'r' },
    payload: {
      lines: Array.from({ length: 2002 }, (_, i) => ({ start: [i, 0, 100], end: [i, 0, -100] })),
      batches: [
        { region_id: 'a', name: 'Area A', bbox: [0, 0, 0, 2000, 100, 10], start: 0, count: 2001 },
        { region_id: 'b', name: 'Area B', bbox: [3000, 0, 0, 3100, 100, 10], start: 2001, count: 1 },
      ],
    },
  };
}

test('drawing is local; terrain polygons belong to this bridge only', async () => {
  const f = await fixture();
  f.bridge.beginDrawing();
  f.events.onEvent({ eventtype: 'EditHelperFinished', coordinates: [[0, 0, 2], [10, 0, 3], [0, 10, 2], [0, 0, 2]] });
  assert.equal(f.calls.regions.length, 1);
  assert.equal(f.calls.elevation.length, 0);
  const region = f.calls.regions[0];
  assert.equal(JSON.stringify(region.bbox), '[0,0,2,10,10,3]');
  f.bridge.showRegion(region);
  f.bridge.showRegion(region);
  assert.equal(f.calls.polygons.length, 1);
  assert.equal(f.calls.polygons[0].style, 10);
  assert.equal(f.calls.polygons[0].frameThickness, 0.04);
  assert.match(f.calls.polygons[0].groupId, /^verdflow_selection_/);
  await f.events.onEvent({ eventtype:'LeftMouseButtonClick',Type:'Polygon',Id:f.calls.polygons[0].id });
  await f.events.onEvent({ eventtype:'LeftMouseButtonClick',Type:'Polygon',Id:'foreign' });
  assert.deepEqual(f.calls.clicked, [region.region_id]);
  f.bridge.clearRegionPreviews();
  assert.deepEqual(f.calls.deleted, [f.calls.polygons[0].id]);
  f.bridge.dispose();
});

test('selection outlines scale with the short side, with bounds for tiny and very large polygons', async () => {
  const f = await fixture();
  for (const [width, height, thickness] of [[1, 1, 0.01], [10, 20, 0.04], [1000, 1000, 1], [1000, 2, 0.01]]) {
    const region = f.createLiveRegion([[0,0,0],[width,0,0],[width,height,0],[0,height,0]], 1);
    f.bridge.showRegion(region);
    assert.equal(f.calls.polygons.at(-1).frameThickness, thickness);
  }
  f.bridge.dispose();
});

test('a small sampling area frames actual XY points, not a 1024m cell or the full ray height', async () => {
  const f = await fixture();
  const c = command();
  c.payload.lines = [[488263,2491165],[488273,2491170]].map(([x,y]) => ({
    start: [x,y,5025], end: [x,y,-4975],
  }));
  c.payload.batches = [{region_id:'small',name:'Small',start:0,count:2,bbox:[]}];
  const windows = f.samplingWindows(c.payload.lines, c.payload.batches);
  assert.equal(windows.length, 1);
  assert.deepEqual(Array.from(windows[0].bbox), [488263,2491165,25,488273,2491170,25]);
  await f.bridge.execute(c);
  assert.equal(f.calls.camera.length, 1);
  assert.deepEqual(Array.from(f.calls.camera[0][0]), [488261,2491163,23,488275,2491172,27]);
  assert.equal(f.calls.lines[0][0].start[2], 5025);
  assert.equal(f.calls.elevation[0][1].length, 2);
  f.bridge.dispose();
});

test('single-point and grid-boundary windows keep tight framing and retain original sample indices', async () => {
  const f = await fixture();
  const lines = [1023,1025,-2].map(x => ({start:[x,10,5008],end:[x,10,-4992]}));
  const batches = [{region_id:'a',name:'A',start:0,count:3,bbox:[]}];
  const windows = f.samplingWindows(lines, batches);
  assert.equal(windows.length, 3);
  for (const w of windows) {
    assert.equal(w.bbox[3] - w.bbox[0], 0);
    assert.equal(w.bbox[4] - w.bbox[1], 0);
    assert.equal(w.bbox[2], 8);
    f.bridge.focusRegion(w);
    const box = f.calls.camera.at(-1)[0];
    assert.equal(box[3] - box[0], 4);
    assert.equal(box[4] - box[1], 4);
  }
  assert.deepEqual(Array.from(windows).flatMap(w => Array.from(w.groups[0].indices)).sort((a,b)=>a-b), [0,1,2]);
  f.bridge.dispose();
});

test('malformed Windows-path import callback is consumed without replacing the panel', async () => {
  const f = await fixture();
  let request;
  f.instance.call = value => {
    request = value;
    queueMicrotask(() => f.instance.onConnectionMessage(String.raw`{"eventtype":"ImportXMLObject","path":"D:\Local\output.xml","resultMessage":"Succeed","type":0}`));
  };
  const item = { sequence: 1, kind: 'import_xml', correlation: command().correlation,
    payload: { xml_path: 'D:\\Local\\output.xml', export_id: 'export' } };
  await f.bridge.execute(item);
  assert.equal(request.data[0].path, 'D:/Local/output.xml');
  assert.equal(f.calls.delivery.length, 1);
  assert.equal(f.calls.delivery[0][2], true);
  assert.equal(f.calls.errors.length, 0);
  f.bridge.dispose();
});

test('SDK callback and document guards are restored on dispose, without overwriting a newer owner', async () => {
  const f = await fixture();
  const guardedWrite = f.document.write;
  const guardedReceiver = f.instance.onConnectionMessage;
  assert.equal(typeof f.window.tick_next, 'function');
  f.bridge.dispose();
  assert.notEqual(f.document.write, guardedWrite);
  assert.notEqual(f.instance.onConnectionMessage, guardedReceiver);
  assert.equal(f.window.tick_next, undefined);
  f.bridge.dispose();
  await assert.rejects(f.bridge.execute(command()), /已关闭/);
  await f.events.onEvent({ eventtype: 'LeftMouseButtonClick', Type: 'TileLayer', MouseClickPoint: [0,0,0] });
  assert.equal(f.calls.clicked.length, 0);

  const newer = await fixture();
  const newerWrite = () => undefined;
  const newerTick = () => undefined;
  const newerReceiver = () => undefined;
  newer.document.write = newerWrite;
  newer.window.tick_next = newerTick;
  newer.instance.onConnectionMessage = newerReceiver;
  newer.bridge.dispose();
  assert.equal(newer.document.write, newerWrite);
  assert.equal(newer.window.tick_next, newerTick);
  assert.equal(newer.instance.onConnectionMessage, newerReceiver);
});

test('terrain events use visible region bounds and precise path hits, not foreign object IDs', async () => {
  const f = await fixture({ containsPoint:true });
  const region = f.createLiveRegion([[100,200,0],[110,200,0],[100,210,0]],1);
  f.bridge.showRegion(region);
  const event = {eventtype:'LeftMouseButtonClick',Type:'TileLayer',Id:'terrain',MouseClickPoint:[101,201,80]};
  await f.events.onEvent(event);
  assert.deepEqual(f.calls.clicked,[region.region_id]);
  for (const changed of [{MouseClickPoint:[0,0,0]},{MouseClickPoint:[NaN,201,0]},
    {MouseClickPoint:['101',201,0]},{Type:'Polygon'},{Type:'ModelPoint'}]) {
    await f.events.onEvent({...event,...changed});
  }
  assert.equal(f.calls.clicked.length,1);
  f.bridge.hideRegion(region.region_id);
  await f.events.onEvent(event);
  assert.equal(f.calls.clicked.length,1);
  f.bridge.dispose();

  const outside = await fixture({containsPoint:false});
  outside.bridge.showRegion(region);
  await outside.events.onEvent(event);
  assert.equal(outside.calls.clicked.length,0);
  outside.bridge.dispose();
});

for (const cef of [true, false]) {
  test(`scene picking is enabled and raw SDK events locate only owned polygons (CEF=${cef})`, async () => {
    const f = await fixture({ cef });
    assert.deepEqual(f.calls.pickMasks, [1]);
    f.events.onReady();
    assert.deepEqual(f.calls.pickMasks, [1]);
    const region = f.createLiveRegion([[0,0,0],[10,0,0],[0,10,0]],1);
    f.bridge.showRegion(region);
    const event = {eventtype:'LeftMouseButtonClick',Type:'Polygon',Id:f.calls.polygons[0].id,GroupID:'',UserData:'',MouseClickPoint:[1,1,0]};
    f.instance.onConnectionMessage(JSON.stringify(event));
    assert.deepEqual(f.calls.clicked,[region.region_id]);
    f.instance.onConnectionMessage(JSON.stringify({...event,Id:'foreign'}));
    f.bridge.hideRegion(region.region_id);
    f.instance.onConnectionMessage(JSON.stringify(event));
    assert.deepEqual(f.calls.clicked,[region.region_id]);
    f.bridge.dispose();
  });

  test(`sampling visits each region, chunks points, submits once (CEF=${cef})`, async () => {
    const f = await fixture({ cef });
    await f.bridge.execute(command());
    assert.equal(f.calls.camera.length, 2);
    assert.equal(f.calls.camera[0][1], -90);
    assert.deepEqual(f.calls.lines.map(lines => lines.length), [1024, 977, 1]);
    assert.equal(f.calls.elevation.length, 1);
    const result = f.calls.elevation[0][1];
    assert.equal(result.length, 2002);
    assert.equal(result[2001].LineIntersectPoint[0], 2001);
    assert.equal(f.calls.cameraReads.length, 1);
    assert.equal(f.calls.sequence[0], 'capture');
    assert.equal(f.calls.sequence.at(-1), 'restore');
    assert.deepEqual(Array.from(f.calls.restoredCamera[0]), [120, 230, 340, -45, 60, 0.8]);
    f.bridge.dispose();
  });
}

test('cancellation during positioning prevents subsequent sampling', async () => {
  const f = await fixture();
  const execution = f.bridge.execute(command());
  f.bridge.cancelSampling();
  await assert.rejects(execution, /取消/);
  assert.equal(f.calls.lines.length, 0);
  assert.equal(f.calls.elevation.length, 0);
  f.bridge.dispose();
});

test('failed region is retried then reported without discarding another region', async () => {
  const f = await fixture({ miss: line => line.start[0] < 2001 });
  await f.bridge.execute(command());
  assert.equal(f.calls.elevation.length, 1);
  assert.equal(f.calls.elevation[0][2][0].region_id, 'a');
  assert.equal(f.calls.elevation[0][1][2001].LineIntersectPoint[2], 12);
  assert.equal(f.calls.restoredCamera.length, 1);
  f.bridge.dispose();
});

test('SDK timeouts terminate regions and report all failures', async () => {
  const f = await fixture({ timeout: true });
  await f.bridge.execute(command());
  assert.equal(f.calls.elevation.length, 1);
  assert.equal(f.calls.elevation[0][2].length, 2);
  f.bridge.dispose();
});

test('late callback from cancelled sampling cannot complete a new request', async () => {
  const f = await fixture({ timeout: true });
  const first = f.bridge.execute(command());
  await new Promise(resolve => setTimeout(resolve, 7));
  f.bridge.cancelSampling();
  await assert.rejects(first);
  const next = f.bridge.execute(command());
  f.window.tick_next({ callbackIndex: 1, intersects: [] });
  await next;
  assert.equal(f.calls.elevation.length, 1);
  assert.equal(f.calls.elevation[0][2].length, 2);
  f.bridge.dispose();
});

test('streamed chunks retain original indices and enforce 2000-point chunks', async () => {
  const f = await fixture();
  const c = command();
  c.payload.lines = Array.from({ length:4501 }, (_,i) => ({start:[i%10,0,100],end:[i%10,0,-100]}));
  c.payload.batches = [{region_id:'a',name:'A',bbox:[0,0,0,10,10,0],start:0,count:4501}];
  c.payload.stream_results = true;
  await f.bridge.execute(c);
  assert.equal(f.calls.camera.length, 1);
  assert.deepEqual(f.calls.chunks.map(c=>c[1].length), [2000,2000,501]);
  assert.equal(f.calls.chunks[2][1][500],4500);
  assert.equal(f.calls.elevation[0][1],null);
  assert.equal(f.calls.elevation[0][3],true);
  f.bridge.dispose();
});

test('1024m windows reduce camera moves and retain every point across 64 regions', async () => {
  const f = await fixture();
  const c = command();
  c.payload.lines = Array.from({length:128}, (_,i) => ({
    start:[(i % 16) * 100, Math.floor(i / 16) * 100, 100],
    end:[(i % 16) * 100, Math.floor(i / 16) * 100, -100],
  }));
  c.payload.batches = Array.from({length:64}, (_,i) => ({
    region_id:`r${i}`, name:`Region ${i}`, start:i*2, count:2, bbox:[],
  }));
  c.payload.stream_results = true;
  const small = f.samplingWindows(c.payload.lines, c.payload.batches, 512);
  const large = f.samplingWindows(c.payload.lines, c.payload.batches);
  assert.equal(small.length,6);
  assert.equal(large.length,2);
  await f.bridge.execute(c);
  assert.equal(f.calls.camera.length,2);
  const indices=f.calls.chunks.flatMap(chunk=>Array.from(chunk[1])).sort((a,b)=>a-b);
  assert.deepEqual(indices,Array.from({length:128},(_,i)=>i));
  assert.equal(f.calls.elevation.length,1);
  assert.equal(f.calls.elevation[0][3],true);
  assert.equal(f.calls.elevation[0][2].length,0);
  f.bridge.dispose();
});

for (const cef of [true, false]) {
  test(`single-ray SDK object is accepted in a multi-window region (CEF=${cef})`, async () => {
    const f = await fixture({cef});
    const c = command();
    c.payload.lines = [10,20,1030].map(x=>({start:[x,0,100],end:[x,0,-100]}));
    c.payload.batches = [{region_id:'a',name:'Area A',start:0,count:3,bbox:[]}];
    c.payload.stream_results = true;
    await f.bridge.execute(c);
    assert.deepEqual(f.calls.lines.map(lines=>lines.length),[2,1]);
    assert.equal(f.calls.chunks.length,2);
    assert.equal(f.calls.chunks[1][1][0],2);
    assert.equal(f.calls.chunks[1][2][0].LineIntersectPoint[2],12);
    assert.equal(f.calls.elevation[0][2].length,0);
    f.bridge.dispose();
  });
}

test('single hit normalization still rejects missing or invalid coordinates', async () => {
  for (const position of [null, [1,2], [1,2,NaN], ['1',2,3]]) {
    const f = await fixture({transformResponse: raw=>({...raw,LineIntersectPoint:position})});
    const c = command();
    c.payload.lines = [{start:[10,0,100],end:[10,0,-100]}];
    c.payload.batches = [{region_id:'a',name:'A',start:0,count:1,bbox:[]}];
    c.payload.stream_results = true;
    await f.bridge.execute(c);
    assert.equal(f.calls.chunks.length,0);
    assert.equal(f.calls.lines.length,2);
    assert.equal(f.calls.elevation[0][2][0].region_id,'a');
    assert.match(f.calls.elevation[0][2][0].reason,/未命中地形/);
    f.bridge.dispose();
  }
});

function importCommand(path = 'D:/Local/current.xml') {
  return { sequence: 2, kind: 'import_xml', correlation: command().correlation,
    payload: { xml_path: path, export_id: path } };
}

test('XML soft timeout warns without failing; only matching full-path receipts finish the import', async () => {
  const f = await fixture({ timeout: true });
  f.instance.call = () => ({ dispatched: true });
  let done = false;
  const execution = f.bridge.execute(importCommand()).then(() => { done = true; });
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(done, false);
  assert.equal(f.calls.delivery.length, 0);
  assert.ok(f.calls.activity.some(message => /仍在等待/.test(message)));
  for (const response of [
    { eventtype: 'ImportXMLObject', path: 'D:/Other/current.xml', resultMessage: 'Succeed' },
    { eventtype: 'ImportXMLObject', resultMessage: 'Succeed' },
    { eventtype: 'ImportXMLObject', path: 'D:/Local/old.xml', resultMessage: 'Failed' },
  ]) f.window.tick_next(response);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(done, false);
  assert.equal(f.calls.errors.length, 0);
  f.window.tick_next({ eventtype: 'ImportXMLObject', path: 'd:\\local\\CURRENT.xml', resultMessage: 'Succeed' });
  await execution;
  assert.equal(f.calls.delivery[0][2], true);
  f.bridge.dispose();
});

test('cancelled import releases the wait; late callbacks and promises cannot settle the next import', async () => {
  const f = await fixture();
  let rejectOld;
  f.instance.call = () => new Promise((_, reject) => { rejectOld = reject; });
  const first = assert.rejects(f.bridge.execute(importCommand('D:/old.xml')), /原生导入可能仍在执行/);
  f.bridge.cancelPendingCommands();
  await first;
  assert.equal(f.calls.delivery.length, 0);
  f.instance.call = () => undefined;
  let completed = false;
  const next = f.bridge.execute(importCommand('D:/next.xml')).then(() => { completed = true; });
  rejectOld(new Error('late dispatch rejection'));
  f.window.tick_next({ eventtype: 'ImportXMLObject', path: 'D:/old.xml', resultMessage: 'Succeed' });
  f.document.write(String.raw`{"eventtype":"ImportXMLObject","path":"D:\old.xml","resultMessage":"Failed"}`);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(completed, false);
  f.window.tick_next({ eventtype: 'ImportXMLObject', path: 'D:/next.xml', resultMessage: 'Succeed' });
  await next;
  assert.equal(f.calls.delivery.length, 1);
  f.bridge.dispose();
});

test('a matching failure is reported, and disposing an import cancels rather than fabricating a receipt', async () => {
  const f = await fixture();
  f.instance.call = () => undefined;
  const failure = assert.rejects(f.bridge.execute(importCommand()), /missing model/);
  f.window.tick_next({ eventtype: 'ImportXMLObject', path: 'D:/Local/current.xml', resultMessage: 'missing model' });
  await failure;
  assert.equal(f.calls.delivery[0][2], false);
  const disposed = assert.rejects(f.bridge.execute(importCommand('D:/other.xml')), /停止等待/);
  f.bridge.dispose();
  await disposed;
  assert.equal(f.calls.delivery.length, 1);
});

test('Promise-based SDK imports still require the matching real receipt', async () => {
  const f = await fixture({ cef: false });
  f.instance.call = () => Promise.resolve({ dispatched: true });
  let done = false;
  const execution = f.bridge.execute(importCommand()).then(() => { done = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(done, false);
  await f.events.onEvent({ eventtype: 'ImportXMLObject', path: 'D:/Local/current.xml', resultMessage: 'Succeed' });
  await execution;
  assert.equal(f.calls.delivery[0][2], true);
  f.bridge.dispose();
});

test('camera is restored exactly once after cancellation during a sampling window', async () => {
  const f = await fixture();
  const execution = f.bridge.execute(command());
  const cancelled = assert.rejects(execution, /取消/);
  await new Promise(resolve => setTimeout(resolve, 1));
  f.bridge.cancelSampling();
  await cancelled;
  assert.equal(f.calls.camera.length, 1);
  assert.equal(f.calls.restoredCamera.length, 1);
  f.bridge.dispose();
  assert.equal(f.calls.restoredCamera.length, 1);
});

test('camera is restored after transport failure; Promise camera reads use the same capture path', async () => {
  const f = await fixture({ cef: false });
  f.instance.camera.get = () => Promise.resolve({ x: 1, y: 2, z: 3, pitch: -50, yaw: 70 });
  const apiError = new Error('transport failed');
  // A failure outside single-region sampling must still restore the scene view.
  f.instance.camera.lookAtBBox = () => { throw apiError; };
  await assert.rejects(f.bridge.execute(command()), /transport failed/);
  assert.deepEqual(Array.from(f.calls.restoredCamera[0]), [1, 2, 3, -50, 70, 0.8]);
  f.bridge.dispose();
});

test('a successful native import is not reported as a native failure when receipt transport fails', async () => {
  const f = await fixture();
  f.api.submitDelivery = async (...args) => { f.calls.delivery.push(args); throw new Error('receipt transport failed'); };
  await assert.rejects(f.bridge.execute(importCommand()), /receipt transport failed/);
  assert.equal(f.calls.delivery.length, 1);
  assert.equal(f.calls.delivery[0][2], true);
  f.bridge.dispose();
});

test('camera read failure is non-blocking and cannot restore an invalid pose', async () => {
  const f = await fixture();
  f.instance.camera.get = () => ({ camera: [1, 2, NaN, -45, 90] });
  await f.bridge.execute(command());
  assert.equal(f.calls.elevation.length, 1);
  assert.equal(f.calls.restoredCamera.length, 0);
  assert.ok(f.calls.errors.some(message => /无法记录视角/.test(message)));
  f.bridge.dispose();
});

for (const cef of [true, false]) {
  test(`request-scoped native completion does not require a second broadcast receipt (CEF=${cef})`, async () => {
    const replies = [
      { result: 0, resultMessage: 'OK' },
      { eventtype: 'ImportXMLObject', resultMessage: 'Succeed' },
      'Succeed',
      JSON.stringify({ result: 0, resultMessage: 'OK' }),
    ];
    for (const reply of replies) {
      const f = await fixture({ cef });
      f.instance.call = () => Promise.resolve(reply);
      await f.bridge.execute(importCommand());
      assert.equal(f.calls.delivery[0][2], true);
      f.bridge.dispose();
    }
  });
}

test('late request-scoped completion cannot finish a new import and direct failures stay failures', async () => {
  const f = await fixture();
  let completeOld;
  f.instance.call = () => new Promise(resolve => { completeOld = resolve; });
  const old = assert.rejects(f.bridge.execute(importCommand('D:/old.xml')), /停止等待/);
  f.bridge.cancelPendingCommands();
  await old;
  f.instance.call = () => undefined;
  let done = false;
  const current = f.bridge.execute(importCommand()).then(() => { done = true; });
  completeOld({ result: 0, resultMessage: 'OK' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(done, false);
  f.window.tick_next({ eventtype: 'ImportXMLObject', path: 'D:/Local/current.xml', resultMessage: 'Succeed' });
  await current;
  f.instance.call = () => Promise.resolve({ result: 1, resultMessage: 'native failure' });
  await assert.rejects(f.bridge.execute(importCommand()), /native failure/);
  assert.equal(f.calls.delivery.at(-1)[2], false);
  f.bridge.dispose();
});
