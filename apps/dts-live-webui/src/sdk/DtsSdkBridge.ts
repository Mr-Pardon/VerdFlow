import { cancelOperation, submitDelivery, submitElevation, submitElevationChunk, submitSelection, elevationPage, type SamplingWindow } from "../api";
import type { ElevationBatch, LiveCommand, LiveRegion, LiveGeometry } from "../types";
import { geometryId, geometryKind } from "../shared/regionLimits";
import type { DtsApi, DtsWindow, PendingSdkRequest, DtsSdkCallbacks } from "./protocol";
import { loadScript, settle, isPromise, asRecord, readableError } from "./runtime";
import { findCoordinates, createLiveRegion, createLivePath, previewFrameThickness } from "./regions";
import { samplingWindows, intersectionRows } from "./elevation";
import { DtsXmlImport, XmlImportCancelled } from "./xmlImport";
import { readCameraPose, type CameraPose } from "./camera";
import { reserveActorCapture, resolveActorCapture, discardActorCapture, nextActorCaptureTile, actorCaptureFrameReady, type ActorCaptureFrame } from "../api";
import { actorPick, ensureActorPickActive, type ActorPick } from "./actorPicking";
import { RegionHitIndex, type HitBox } from "./regionHitIndex";
import { brushScreenSamples, brushFootprint, brushIntersectsRings } from "../shared/brushFootprint";

const CAMERA_FLIGHT_SECONDS = 0.8;
const CLICK_PICK_MASK = 1;
const ACTOR_PICK_COLOR = [0.02, 0.14, 0.48, 0.08];
const regionFill = (enabled: boolean) => [0.14, 0.63, 0.59, enabled ? 0.3 : 0.05];
const regionFrame = (enabled: boolean) => enabled ? [0.36, 0.95, 0.82, 0.95] : [0.72, 0.81, 0.78, 0.46];

function terrainPoint(value: unknown): [number, number, number] | undefined {
  if (Array.isArray(value) && value.length === 3 && value.every(v => typeof v === "number" && Number.isFinite(v))) {
    return value as [number, number, number];
  }
}

const signedArea = (ring: number[][]) => {
  const origin = ring[0]!;
  return ring.reduce((sum, point, index) => {
    const next = ring[(index + 1) % ring.length]!;
    return sum + (point[0]! - origin[0]!) * (next[1]! - origin[1]!) -
      (next[0]! - origin[0]!) * (point[1]! - origin[1]!);
  }, 0);
};

function polygonCoordinates(region: LiveRegion) {
  if (!region.holes?.length) return region.coordinates;
  const orientation = Math.sign(signedArea(region.coordinates));
  return [region.coordinates, ...region.holes.map(ring =>
    Math.sign(signedArea(ring)) === orientation ? [...ring].reverse() : ring)];
}

export class DtsSdkBridge {
  private disposed = false;
  private api: DtsApi | null = null;
  private selection: LiveCommand | null = null;
  private draftDrawing = false;
  private drawingKind: "polygon" | "polyline" = "polygon";
  private drawingNumber = 0;
  private samplingGeneration = 0;
  private mousePickingConfigured = false;
  private readonly previewGroup = `verdflow_selection_${crypto.randomUUID()}`;
  private readonly previewIds = new Map<string, string>();
  private readonly previewKinds = new Map<string, "polygon" | "polyline">();
  private readonly previewRegions = new Map<string, string>();
  private readonly visiblePreviewIds = new Set<string>();
  private readonly previewShapes = new Map<string, { bbox: number[]; path?: Path2D; rings: number[][][] }>();
  private regionHitIndex: RegionHitIndex | null = null;
  private resetRegionBrush: (() => void) | null = null;
  private brushCapture = false;
  private brushPressed = false;
  private readonly pendingRegionColors = new Map<string, boolean>();
  private regionColorFrame?: number;
  private pickContext: CanvasRenderingContext2D | null = null;
  private readonly pendingRequests = new Map<number, PendingSdkRequest>();
  private readonly xmlImport: DtsXmlImport;
  private savedCamera: { generation: number; pose: CameraPose } | null = null;
  private readonly sceneWaits = new Map<number, () => void>();
  private restoreTickCallback: (() => void) | null = null;
  private actorPickListener: ((point: ActorPick | null) => void) | null = null;
  private actorRangeDrawn: ((region: LiveRegion | null) => void) | null = null;
  private actorRangeId: string | null = null;
  private actorSceneChanged: (() => void) | null = null;
  private actorHighlight: { id: string; objectIds: string[] } | null = null;
  private actorHighlightRevision = 0;
  private actorHighlightQueue = Promise.resolve();
  private actorCaptureId: string | null = null;

  constructor(private readonly callbacks: DtsSdkCallbacks) {
    this.xmlImport = new DtsXmlImport(message => this.callbacks.onActivity(message));
  }

  async initialize() {
    await loadScript("/sdk/ac_conf.js");
    if (this.disposed) return;
    await loadScript("/sdk/ac.min.js");
    if (this.disposed) return;
    const dtsWindow = window as DtsWindow;
    if (typeof dtsWindow.DigitalTwinAPI !== "function") {
      throw new Error("DTS Explorer SDK 未注册 DigitalTwinAPI");
    }
    if (!dtsWindow.ue?.internal) {
      throw new Error("当前页面未运行在 DTS Explorer 内部 WebUI 环境");
    }
    this.api = new dtsWindow.DigitalTwinAPI(null, {
      onReady: () => this.onReady(),
      onEvent: (event) => void this.onEvent(event),
      // Scene interaction payloads are SDK diagnostics, not workflow status.
      onLog: () => undefined,
    });
    this.xmlImport.install(this.api);
    this.installTickCallback();
    if (this.api.isCalledInCEF) this.onReady();
  }

  private onReady() {
    if (!this.api || this.disposed) return;
    if (!this.mousePickingConfigured && this.api.settings?.setMousePickMask) {
      try {
        // Listening to onEvent does not enable Explorer's scene picking.
        const result = this.api.settings.setMousePickMask(CLICK_PICK_MASK);
        this.mousePickingConfigured = true;
        void settle(result).catch(error => {
          this.mousePickingConfigured = false;
          this.callbacks.onError(`DTS 左键拾取开启失败：${readableError(error)}`);
        });
      } catch (error) {
        this.callbacks.onError(`DTS 左键拾取开启失败：${readableError(error)}`);
      }
    }
    this.callbacks.onReady();
  }

  async execute(command: LiveCommand) {
    if (this.disposed) throw new Error("DTS Explorer SDK 已关闭");
    if (!this.api) throw new Error("DTS Explorer SDK 尚未就绪");
    if (command.kind === "begin_polygon_selection") {
      this.beginPolygonSelection(command);
      return;
    }
    if (command.kind === "sample_elevation") {
      await this.sampleElevation(command);
      return;
    }
    if (command.kind === "import_xml") {
      await this.importXml(command);
    }
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.resetRegionBrush?.();
    this.stopRegionBrush();
    if (this.regionColorFrame !== undefined) window.cancelAnimationFrame(this.regionColorFrame);
    this.pendingRegionColors.clear();
    this.actorPickListener = null;
    this.actorSceneChanged = null;
    this.releaseActorCapture();
    this.cancelDrawing();
    this.cancelSampling();
    for (const index of this.pendingRequests.keys()) this.failPendingRequest(index, "场景已关闭");
    this.clearRegionPreviews();
    this.xmlImport.dispose();
    this.restoreTickCallback?.();
    this.restoreTickCallback = null;
    this.pickContext = null;
  }

  beginDrawing(kind: "polygon" | "polyline" = "polygon") {
    if (!this.api?.editHelper) throw new Error("DTS 选面工具不可用");
    if (this.draftDrawing || this.actorRangeDrawn) return;
    this.draftDrawing = true;
    this.drawingKind = kind;
    try {
      this.api.editHelper.setParam(0, kind === "polyline" ? 0 : 1, [53, 183, 159, 255]);
      this.api.editHelper.start();
    } catch (error) {
      this.draftDrawing = false;
      throw error;
    }
  }

  cancelDrawing() {
    this.cancelActorRangeDrawing();
    const wasDrawing = this.draftDrawing || this.selection !== null;
    this.draftDrawing = false;
    this.selection = null;
    if (wasDrawing) this.api?.editHelper?.cancel?.();
    this.callbacks.onDrawingFinished();
  }

  hasRegion(id: string) { return this.previewIds.has(id); }

  startRegionBrush(reset: () => void) {
    if (!this.api?.coord?.screen2World) {
      throw new Error("当前 DTS Explorer 不支持笔刷选区。");
    }
    this.resetRegionBrush = reset;
    this.setRegionBrushCapture(true);
  }

  setRegionBrushCapture(capturing: boolean) {
    if (capturing && !this.resetRegionBrush) return;
    if (!capturing) this.setRegionBrushPressed(false);
    this.brushCapture = capturing;
  }

  setRegionBrushPressed(pressed: boolean) {
    if (this.brushPressed === pressed || (pressed && !this.brushCapture)) return;
    this.brushPressed = pressed;
    if (!pressed) {
      for (const [index, request] of this.pendingRequests) {
        if (request.kind === "brush") this.failPendingRequest(index, "笔刷已结束");
      }
    }
  }

  stopRegionBrush() {
    this.resetRegionBrush = null;
    this.setRegionBrushCapture(false);
  }

  async sampleRegionBrushScreen(x: number, y: number, seen: ReadonlySet<string>) {
    const coord = this.api?.coord;
    if (!this.brushPressed || !coord?.screen2World) return;
    const points = await Promise.all(brushScreenSamples(x, y).map(async ([px, py]) => {
      if (px < 0 || py < 0 || px >= window.innerWidth || py >= window.innerHeight) return;
      try {
        const reply = await this.requestSdk(callback => coord.screen2World!(px, py, callback), "DTS 场景拾取超时", 500, "brush");
        const result = asRecord(typeof reply === "string" ? JSON.parse(reply) : reply);
        if (result?.result != null && result.result !== 0) return;
        return terrainPoint(result?.worldLocation);
      } catch { return; }
    }));
    if (!this.brushPressed) return;
    const footprint = brushFootprint(points);
    if (!footprint) return;
    this.pickContext ??= document.createElement("canvas").getContext("2d");
    return this.getRegionHitIndex().intersect(footprint.bounds, id => {
      if (seen.has(id) || !this.visiblePreviewIds.has(id) || this.previewKinds.get(id) !== "polygon") return false;
      const { bbox, path, rings } = this.previewShapes.get(id)!;
      return brushIntersectsRings(footprint, rings, point =>
        Boolean(path && this.pickContext?.isPointInPath(path, point[0]! - bbox[0]!, point[1]! - bbox[1]!, "evenodd")));
    });
  }

  startActorPicking(listener: (point: ActorPick | null) => void, onSceneChanged?: () => void) {
    if (!this.api?.camera?.exportOrthoImage || !this.api.tileLayer?.getActorInfo || !this.api.tileLayer.highlightActorsWithColor
        || !this.api.tileLayer.stopHighlightActors) throw new Error("当前 DTS Explorer 不支持绿地拾取。");
    this.actorPickListener = listener;
    this.actorSceneChanged = onSceneChanged ?? null;
  }

  stopActorPicking(finished = true) {
    this.actorPickListener = null;
    if (finished) this.actorSceneChanged = null;
  }

  beginActorRangeDrawing(done: (region: LiveRegion | null) => void) {
    const edit = this.api?.editHelper;
    if (!edit || !this.api?.polygon) throw new Error("当前 DTS Explorer 不支持绘制提取范围。");
    if (this.actorRangeDrawn || this.draftDrawing || this.selection) throw new Error("请先完成当前绘制。");
    this.actorRangeDrawn = done;
    try {
      edit.setParam(0, 1, [251, 190, 87, 255]);
      edit.start();
    } catch (failure) {
      this.actorRangeDrawn = null;
      throw failure;
    }
  }

  cancelActorRangeDrawing() {
    if (!this.actorRangeDrawn) return;
    this.actorRangeDrawn = null;
    this.api?.editHelper?.cancel?.();
  }

  showActorRange(region: LiveRegion) {
    const api = this.api?.polygon;
    if (!api || this.disposed) return;
    const clear = this.clearActorRangePreview();
    void clear.catch(failure => this.callbacks.onError(readableError(failure)));
    const id = this.actorRangeId = `${this.previewGroup}_actor_range_${region.region_id}`;
    // This polygon is a temporary guide, not a selectable planting preview.
    const object = this.previewObject(region);
    void settle(api.add({ ...object, id, color: [1, 0.72, 0.27, 0.06],
      frameColor: [1, 0.77, 0.34, 1], frameThickness: Math.max(0.15, Number(object.frameThickness)), priority: 2 }))
      .catch(failure => this.callbacks.onError(readableError(failure)));
  }

  async clearActorRangePreview() {
    if (!this.actorRangeId) return;
    const id = this.actorRangeId;
    await settle(this.api?.polygon?.delete(id));
    if (this.actorRangeId === id) this.actorRangeId = null;
  }

  previewActorPick(pick: ActorPick | null) {
    const revision = ++this.actorHighlightRevision;
    const apply = this.actorHighlightQueue.then(async () => {
      if (this.disposed || revision !== this.actorHighlightRevision) return;
      const tiles = this.api?.tileLayer;
      if (!tiles) return;
      const current = this.actorHighlight;
      if (current) {
        await this.requestActor("DTS 取消构件高亮", callback => tiles.stopHighlightActors!(current, callback), 2000);
        if (this.actorHighlight === current) this.actorHighlight = null;
      }
      if (!pick || this.disposed || revision !== this.actorHighlightRevision) return;
      const target = this.actorHighlight = { id: pick.layer, objectIds: [pick.actor] };
      // Explorer 7.1 handles the array overload reliably.
      await this.requestActor("DTS 构件高亮", callback =>
        tiles.highlightActorsWithColor!([{ ...target, color: ACTOR_PICK_COLOR, bWireframe: false }], callback), 2000);
    });
    this.actorHighlightQueue = apply.catch(() => undefined);
    return apply;
  }

  releaseActorCapture() {
    this.actorHighlightRevision++;
    void this.clearActorRangePreview().catch(() => undefined);
    if (this.actorHighlight) {
      try { this.api?.tileLayer?.stopHighlightActors?.(this.actorHighlight, () => undefined); }
      catch { /* Explorer may already have closed. */ }
      this.actorHighlight = null;
    }
    if (this.actorCaptureId) void discardActorCapture(this.actorCaptureId).catch(() => undefined);
  }

  private async requestActor(action: string, call: (callback?: (value: unknown) => void) => unknown, timeout = 18000) {
    let raw = await this.requestSdk(call, `${action}超时`, timeout, "actor");
    if (typeof raw === "string") {
      try { raw = JSON.parse(raw); } catch { /* The embedded bridge can acknowledge without a result. */ }
    }
    const value = asRecord(raw)?.result;
    const code = (typeof value === "number" || (typeof value === "string" && /^\d{1,6}$/.test(value)))
      ? Number(value) : null;
    if (code !== null && code !== 0) throw new Error(`${action}失败（DTS 错误码 ${code}）`);
    if (raw === false || value === false || (!this.api?.isCalledInCEF && code === null)) throw new Error(`${action}失败`);
    return asRecord(raw)?.data ?? raw;
  }

  async extractActor(pick: ActorPick, signal: AbortSignal, clip?: LiveRegion["coordinates"]) {
    const api = this.api;
    const camera = api?.camera;
    const tiles = api?.tileLayer;
    if (!api || !camera?.exportOrthoImage || !tiles?.getActorInfo || !tiles.highlightActorsWithColor || !tiles.stopHighlightActors) {
      throw new Error("当前 DTS Explorer 不支持绿地拾取。");
    }
    const ensure = () => {
      if (this.disposed) throw new Error("DTS Explorer SDK 已关闭");
      ensureActorPickActive(signal);
    };
    const request = this.requestActor.bind(this);
    ensure();
    const target = { id: pick.layer, objectIds: [pick.actor] };
    const data = await request("DTS 读取构件范围", callback => tiles.getActorInfo!(target, callback), 2500);
    const info = asRecord(Array.isArray(data) ? data[0] : data);
    const min = terrainPoint(info?.boundsMin), max = terrainPoint(info?.boundsMax);
    if (!min || !max || min.some((n, i) => n > max[i]!)) {
      throw new Error("未读到完整构件范围，请等待场景加载后重新提取。");
    }
    await this.previewActorPick(null);
    await this.clearActorRangePreview();
    ensure();
    const waitForStyle = () => new Promise(resolve => window.setTimeout(resolve, 200));
    const { capture_id: capture } = await reserveActorCapture(pick.point, [min, max], clip);
    this.actorCaptureId = capture;
    const collect = async (frame: ActorCaptureFrame) => {
      while (true) {
        ensure();
        const { tile } = await nextActorCaptureTile(capture, frame);
        ensure();
        if (!tile) return;
        await request("DTS 正交出图", callback =>
          camera.exportOrthoImage!(tile.path, [tile.size, tile.size], tile.width, tile.location, [-90, 0, 0], [0, 0, 0, 1], callback));
        const deadline = Date.now() + 15000;
        // The embedded bridge acknowledges dispatch before file completion.
        while (true) {
          ensure();
          if ((await actorCaptureFrameReady(capture, frame, tile.tile)).ready) break;
          if (Date.now() >= deadline) throw new Error("DTS 未写完正交影像，请确认场景已加载后重试。");
          await new Promise(resolve => window.setTimeout(resolve, 150));
        }
      }
    };
    try {
      try {
        for (const [frame, color] of [["magenta", [1, 0, 1, 1]], ["cyan", [0, 1, 1, 1]]] as const) {
          ensure();
          this.actorHighlight = target;
          await request("DTS 构件高亮", callback =>
            tiles.highlightActorsWithColor!([{ ...target, color: [...color], bWireframe: false }], callback));
          await waitForStyle();
          await collect(frame);
        }
      } finally {
        if (this.actorHighlight === target) {
          if (!this.disposed) await request("DTS 取消构件高亮", callback => tiles.stopHighlightActors!(target, callback));
          this.actorHighlight = null;
        }
      }
      await waitForStyle();
      await collect("normal");
      ensure();
      const result = await resolveActorCapture(capture);
      ensure();
      return result;
    } finally {
      this.actorCaptureId = null;
      await discardActorCapture(capture).catch(() => undefined);
    }
  }

  showRegion(region: LiveGeometry) {
    const key = geometryId(region), kind = geometryKind(region);
    const api = kind === "polyline" ? this.api?.polyline : this.api?.polygon;
    if (!api) throw new Error(kind === "polyline" ? "DTS 路径预览接口不可用" : "DTS 选区预览接口不可用");
    const existing = this.previewIds.get(key);
    if (existing) {
      if (!this.visiblePreviewIds.has(key)) {
        api.show(existing);
        this.visiblePreviewIds.add(key);
      }
      return;
    }
    const object = this.previewObject(region);
    api.add(object);
    this.trackPreview(region, object.id);
  }

  async addImportedPage(regions: LiveRegion[]) {
    const api = this.api?.polygon;
    if (!api || this.disposed) throw new Error("DTS 选区预览接口不可用");
    const objects = regions.map(r => this.previewObject(r));
    await api.add(objects);
    if (this.disposed) {
      for (const object of objects) api.delete(object.id);
      return;
    }
    regions.forEach((r, i) => this.trackPreview(r, objects[i]!.id));
  }

  private previewObject(region: LiveGeometry) {
    const id = `${this.previewGroup}_${geometryId(region)}`, kind = geometryKind(region);
    return {
      id, groupId: this.previewGroup, coordinates: kind === "polyline" ? region.coordinates : polygonCoordinates(region as LiveRegion),
      coordinateType: 0, style: kind === "polyline" ? 0 : 10, shape: 0, thickness: Math.max(0.03, previewFrameThickness(region)), color: kind === "polyline" ? [0.14, 0.63, 0.59, 1] : regionFill(true),
      frameColor: regionFrame(true), frameThickness: previewFrameThickness(region),
      range: [1, 1000000], depthTest: false, priority: 1,
    };
  }

  private trackPreview(region: LiveGeometry, id: string) {
    const key = geometryId(region), kind = geometryKind(region);
    this.previewIds.set(key, id);
    this.previewKinds.set(key, kind);
    this.previewRegions.set(id, key);
    this.visiblePreviewIds.add(key);
    const path = typeof Path2D === "undefined" ? undefined : new Path2D();
    const rings = [region.coordinates, ...(region.holes ?? [])];
    if (path) {
      for (const ring of rings) {
        ring.forEach((point, index) => {
          if (index === 0) path.moveTo(point[0]! - region.bbox[0]!, point[1]! - region.bbox[1]!);
          else path.lineTo(point[0]! - region.bbox[0]!, point[1]! - region.bbox[1]!);
        });
        if (kind === "polygon") path.closePath();
      }
    }
    // Borrow the rings for brush boundary tests; do not copy their coordinates.
    this.previewShapes.set(key, { bbox: [...region.bbox], path, rings });
    this.regionHitIndex = null;
  }

  setRegionSelectionAppearance(regionId: string, enabled: boolean) {
    const polygon = this.api?.polygon;
    if ((!polygon?.update && !polygon?.setColor) || this.previewKinds.get(regionId) !== "polygon") return;
    this.pendingRegionColors.set(regionId, enabled);
    if (this.regionColorFrame === undefined) this.regionColorFrame = window.requestAnimationFrame(this.flushRegionColors);
  }

  private flushRegionColors = () => {
    this.regionColorFrame = undefined;
    if (this.disposed) return;
    const polygon = this.api?.polygon;
    const updates: { id: string; color: number[]; frameColor: number[] }[] = [];
    for (const [region, enabled] of this.pendingRegionColors) {
      this.pendingRegionColors.delete(region);
      const id = this.previewIds.get(region);
      if (id) updates.push({ id, color: regionFill(enabled), frameColor: regionFrame(enabled) });
      if (updates.length >= 64) break;
    }
    if (updates.length) {
      if (polygon?.update) {
        try { void settle(polygon.update(updates)).catch(() => undefined); }
        catch { /* Selection state remains valid even if Explorer cannot repaint. */ }
      } else if (polygon?.setColor) {
        for (const { id, color } of updates) {
          try { void settle(polygon.setColor(id, color)).catch(() => undefined); }
          catch { /* Keep repainting the rest of the batch. */ }
        }
      }
    }
    if (this.pendingRegionColors.size) this.regionColorFrame = window.requestAnimationFrame(this.flushRegionColors);
  };

  hideRegion(regionId: string) {
    const id = this.previewIds.get(regionId);
    if (!id || !this.visiblePreviewIds.has(regionId)) return;
    (this.previewKinds.get(regionId) === "polyline" ? this.api?.polyline : this.api?.polygon)?.hide(id);
    this.visiblePreviewIds.delete(regionId);
  }

  removeRegion(regionId: string) {
    const id = this.previewIds.get(regionId);
    if (!id) return;
    (this.previewKinds.get(regionId) === "polyline" ? this.api?.polyline : this.api?.polygon)?.delete(id);
    this.previewIds.delete(regionId);
    this.previewKinds.delete(regionId);
    this.visiblePreviewIds.delete(regionId);
    this.previewRegions.delete(id);
    this.previewShapes.delete(regionId);
    this.pendingRegionColors.delete(regionId);
    this.regionHitIndex = null;
  }

  clearRegionPreviews() {
    for (const regionId of [...this.previewIds.keys()]) {
      try { this.removeRegion(regionId); }
      catch (error) { this.callbacks.onError(`选区预览清理失败：${readableError(error)}`); }
    }
  }

  focusRegion(region: Pick<LiveRegion, "bbox">) {
    if (!this.api?.camera) throw new Error("DTS 相机定位接口不可用");
    const bbox = [...region.bbox];
    for (let axis = 0; axis < 3; axis += 1) {
      const margin = Math.max(2, (bbox[axis + 3]! - bbox[axis]!) * 0.15);
      bbox[axis]! -= margin;
      bbox[axis + 3]! += margin;
    }
    this.api.camera.lookAtBBox(bbox, -90, 0, CAMERA_FLIGHT_SECONDS);
  }

  cancelSampling() {
    this.samplingGeneration += 1;
    for (const [timer, resolve] of this.sceneWaits) {
      window.clearTimeout(timer);
      resolve();
    }
    this.sceneWaits.clear();
    this.restoreSamplingCamera();
    for (const [index, request] of this.pendingRequests) {
      if (request.kind === "sampling") this.failPendingRequest(index, "高程采样已取消");
    }
  }

  cancelPendingCommands() {
    this.cancelSampling();
    return this.xmlImport.cancel();
  }

  private beginPolygonSelection(command: LiveCommand) {
    if (!this.api?.editHelper) {
      throw new Error("DTS 选面工具不可用");
    }
    this.selection = command;
    this.api.editHelper.setParam(0, 1, [36, 160, 150, 255]);
    this.api.editHelper.start();
    this.callbacks.onActivity("请在场景中绘制一个闭合种植区域");
  }

  private async onEvent(event: unknown) {
    if (this.disposed) return;
    if (this.xmlImport.consume(event)) return;
    const document = asRecord(event);
    if (document?.eventtype === "LoadProjectFinished") {
      this.actorSceneChanged?.();
      this.resetRegionBrush?.();
      this.stopRegionBrush();
    }
    if (document?.eventtype === "LeftMouseButtonClick") {
      if (this.brushCapture || this.actorRangeDrawn) return;
      if (this.actorPickListener) {
        this.actorPickListener(actorPick(document));
        return;
      }
      let regionId = ["Polygon", "Polyline"].includes(document.Type as string) && typeof document.Id === "string"
        ? this.previewRegions.get(document.Id) : undefined;
      if (regionId && !this.visiblePreviewIds.has(regionId)) regionId = undefined;
      // OnTerrain polygons may be picked as the underlying terrain in Explorer.
      // Never reinterpret a click on somebody else's Polygon as our selection.
      if (!regionId && document.Type === "TileLayer") {
        const point = terrainPoint(document.MouseClickPoint);
        if (point) regionId = this.regionAtTerrainPoint(point);
      }
      if (regionId) this.callbacks.onRegionClicked?.(regionId);
      return;
    }
    if (document?.eventtype === "EditHelperFinished" && this.actorRangeDrawn) {
      const done = this.actorRangeDrawn;
      this.actorRangeDrawn = null;
      let range: LiveRegion | null = null;
      try {
        const coordinates = findCoordinates(event);
        if (!coordinates) throw new Error("未完成提取范围，请重新绘制。");
        range = createLiveRegion(coordinates, 1);
      } catch (error) {
        this.callbacks.onError(readableError(error));
      }
      done(range);
      return;
    }
    if (document?.eventtype === "EditHelperFinished" && this.draftDrawing) {
      this.draftDrawing = false;
      this.callbacks.onDrawingFinished();
      try {
        const coordinates = findCoordinates(event);
        if (!coordinates) throw new Error("DTS 选面结果没有可识别的坐标");
        if (this.drawingKind === "polyline") this.callbacks.onPathDrawn?.(createLivePath(coordinates, ++this.drawingNumber));
        else this.callbacks.onRegionDrawn(createLiveRegion(coordinates, ++this.drawingNumber));
      } catch (error) {
        this.callbacks.onError(readableError(error));
      }
      return;
    }
    if (document?.eventtype !== "EditHelperFinished" || !this.selection) return;
    const command = this.selection;
    this.selection = null;
    try {
      const coordinates = findCoordinates(event);
      if (!coordinates) throw new Error("DTS 选面结果没有可识别的坐标");
      await submitSelection(command.correlation, coordinates);
      this.callbacks.onActivity(`已接收 ${coordinates.length} 个轮廓点`);
      this.callbacks.onCommandComplete();
    } catch (error) {
      this.callbacks.onError(readableError(error));
      await cancelOperation().catch(() => undefined);
      this.callbacks.onCommandComplete();
    }
  }

  private getRegionHitIndex() {
    if (!this.regionHitIndex) {
      const boxes: HitBox[] = [];
      for (const [id, { bbox }] of this.previewShapes) {
        const [minX, minY, , maxX, maxY] = bbox as [number, number, number, number, number, number];
        const padding = this.previewKinds.get(id) === "polyline" ? 0.5 : 0;
        boxes.push({ id, order: boxes.length, bounds: [minX - padding, minY - padding, maxX + padding, maxY + padding] });
      }
      this.regionHitIndex = new RegionHitIndex(boxes);
    }
    return this.regionHitIndex;
  }

  private regionAtTerrainPoint([x, y]: [number, number, number]): string | undefined {
    return this.getRegionHitIndex().pick(x, y, regionId => {
      if (!this.visiblePreviewIds.has(regionId)) return false;
      const isLine = this.previewKinds.get(regionId) === "polyline";
      const shape = this.previewShapes.get(regionId);
      if (!shape?.path) return false;
      const [minX, minY] = shape.bbox as [number, number];
      this.pickContext ??= document.createElement("canvas").getContext("2d");
      if (!this.pickContext) return false;
      if (isLine) {
        this.pickContext.lineWidth = 1;
        return this.pickContext.isPointInStroke(shape.path, x - minX, y - minY);
      }
      return this.pickContext.isPointInPath(shape.path, x - minX, y - minY, "evenodd");
    });
  }

  private async sampleElevation(command: LiveCommand) {
    const generation = ++this.samplingGeneration;
    try {
      if (this.api?.camera?.get && this.api.camera.set) {
        try {
          const pose = readCameraPose(await this.requestSdk(
            callback => this.api!.camera!.get!(callback), "DTS 相机位置读取超时", 3000,
          ));
          if (generation === this.samplingGeneration) this.savedCamera = { generation, pose };
        } catch (error) {
          if (generation === this.samplingGeneration) this.callbacks.onError(`无法记录视角，采样后将无法恢复：${readableError(error)}`);
        }
      }
      if (generation !== this.samplingGeneration) throw new Error("高程采样已取消");
      await this.sampleElevationWindows(command, generation);
    } finally {
      if (this.restoreSamplingCamera(generation)) {
        // Finish the camera flight before the next import can block DTS again.
        await this.waitForScene(CAMERA_FLIGHT_SECONDS * 1000);
      }
    }
  }

  private restoreSamplingCamera(generation?: number) {
    const saved = this.savedCamera;
    if (!saved || (generation !== undefined && saved.generation !== generation)) return false;
    this.savedCamera = null;
    try {
      void settle(this.api?.camera?.set?.(...saved.pose, CAMERA_FLIGHT_SECONDS)).catch(error => {
        this.callbacks.onError(`恢复相机视角失败：${readableError(error)}`);
      });
      return true;
    } catch (error) { this.callbacks.onError(`恢复相机视角失败：${readableError(error)}`); return false; }
  }

  private waitForScene(milliseconds: number): Promise<void> {
    return new Promise(resolve => {
      const timer = window.setTimeout(() => { this.sceneWaits.delete(timer); resolve(); }, milliseconds);
      this.sceneWaits.set(timer, resolve);
    });
  }

  private async sampleElevationWindows(command: LiveCommand, generation: number) {
    const pageCount = Number(command.payload.page_count ?? 0);
    const lines = Array.isArray(command.payload.lines) ? command.payload.lines : [];
    if (!this.api?.tools?.linesIntersect || (!pageCount && !lines.length)) {
      throw new Error("DTS 高程采样工具不可用");
    }
    const batches = command.payload.batches as ElevationBatch[] | undefined;
    const plan = batches?.length ? batches : [{
      region_id: "selection", name: "选区", bbox: [], start: 0, count: lines.length,
    }];
    const streamed = command.payload.stream_results === true;
    const results: unknown[] = streamed ? [] : Array(lines.length).fill(null);
    const failures = new Map<string, { region_id?: string; path_id?: string; reason: string }>();
    const assertActive = () => {
      if (generation !== this.samplingGeneration) throw new Error("高程采样已取消");
    };
    let windowCount = Number(command.payload.window_count ?? 0);
    async function* windows(): AsyncGenerator<SamplingWindow> {
      if (pageCount) {
        for (let page = 0; page < pageCount; page++) {
          assertActive();
          yield* (await elevationPage(command.correlation, page)).windows;
        }
      } else {
        const local = samplingWindows(lines, plan);
        windowCount = local.length;
        for (const [number, window] of local.entries()) yield { number, ...window };
      }
    }
    let focused = -1;
    for await (const window of windows()) {
      const index = window.number;
      assertActive();
      if (window.groups.every(group => failures.has(group.selection_id))) continue;
      if (focused !== index) {
        this.callbacks.onActivity(`定位采样窗口 ${index + 1}/${windowCount}`);
        this.focusRegion(window);
        await this.waitForScene(1400);
        focused = index;
      }
      assertActive();
      for (const group of window.groups) {
        if (failures.has(group.selection_id)) continue;
        for (let offset = 0; offset < group.indices.length; offset += 2000) {
          const indices = group.indices.slice(offset, offset + 2000);
          const chunk = group.lines ? group.lines.slice(offset, offset + 2000) : indices.map(i => lines[i]);
          let rows: unknown[] | null = null;
          for (let attempt = 0; attempt < 2; attempt++) {
            assertActive();
            this.callbacks.onActivity(`采样窗口 ${index + 1}/${windowCount} · ${group.name}${attempt ? " · 重试" : ""}`);
            try {
              if (attempt) {
                this.focusRegion(window);
                await this.waitForScene(1400);
                assertActive();
              }
              rows = intersectionRows(await this.sampleLines(chunk), chunk.length);
              assertActive();
              break;
            } catch (error) {
              assertActive();
              if (attempt === 1) {
                const path = group.path_id || (!pageCount && plan.find(b => b.path_id === group.selection_id)?.path_id);
                failures.set(group.selection_id, { ...(path ? {path_id: group.selection_id} : {region_id: group.selection_id}), reason: readableError(error) });
              }
            }
          }
          if (!rows) break;
          // Transport errors are task failures, not evidence of a bad polygon.
          if (streamed) await submitElevationChunk(command.correlation, indices, rows);
          else indices.forEach((i, j) => { results[i] = rows![j]; });
        }
      }
    }
    assertActive();
    await submitElevation(command.correlation, streamed ? null : results, [...failures.values()], streamed);
    this.callbacks.onActivity(failures.size ? `采样完成，跳过 ${failures.size} 个失败选区` : "采样完成，正在整理高程结果并生成 XML");
    this.callbacks.onCommandComplete();
  }

  private sampleLines(lines: unknown[]): Promise<unknown> {
    return this.requestSdk(callback => this.api!.tools!.linesIntersect(
      lines, false, false, callback,
    ), "DTS 高程采样等待超时", 30_000);
  }

  private requestSdk(
    invoke: (callback?: (value: unknown) => void) => unknown,
    message: string,
    milliseconds: number,
    kind: PendingSdkRequest["kind"] = "sampling",
  ): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const index = Number(this.api?.callbackIndex ?? 0) + 1;
      const timeout = window.setTimeout(() => {
        this.failPendingRequest(index, message);
      }, milliseconds);
      this.pendingRequests.set(index, { resolve, reject, timeout, kind });
      try {
        const callback = (value: unknown) => { this.completePendingRequest(index, value); };
        const result = invoke(this.api?.isCalledInCEF ? undefined : callback);
        if (isPromise(result)) {
          void result.then(value => {
            if (value != null || (kind === "actor" && this.api?.isCalledInCEF)) callback(value);
          }, error => this.failPendingRequest(index, readableError(error)));
        } else if (result != null) callback(result);
      } catch (error) {
        this.failPendingRequest(index, readableError(error));
      }
    });
  }

  private completePendingRequest(index: number, result: unknown) {
    const pending = this.pendingRequests.get(index);
    if (!pending) return;
    this.pendingRequests.delete(index);
    window.clearTimeout(pending.timeout);
    pending.resolve(result);
  }

  private failPendingRequest(index: number, message: string) {
    const pending = this.pendingRequests.get(index);
    if (!pending) return;
    this.pendingRequests.delete(index);
    window.clearTimeout(pending.timeout);
    pending.reject(new Error(message));
  }

  private async importXml(command: LiveCommand) {
    const xmlPath = command.payload.xml_path;
    const exportId = command.payload.export_id;
    if (typeof xmlPath !== "string" || typeof exportId !== "string") {
      throw new Error("XML 导入命令缺少必要参数");
    }
    const commandApi =
      typeof this.api?.call === "function"
        ? this.api
        : (window as DtsWindow).fdapi;
    if (!commandApi || typeof commandApi.call !== "function") {
      throw new Error("DTS XML 导入接口不可用");
    }
    try {
      this.xmlImport.install(this.api!);
      await this.xmlImport.execute(commandApi, xmlPath);
    } catch (error) {
      if (error instanceof XmlImportCancelled) throw error;
      const message = readableError(error);
      await submitDelivery(command.correlation, exportId, false, message).catch(() => undefined);
      throw error;
    }
    // A transport failure while acknowledging success must not become a false
    // native-import failure or issue the same import a second time.
    await submitDelivery(command.correlation, exportId, true);
    this.callbacks.onActivity("植被已加载到当前 DTS 场景");
    this.callbacks.onCommandComplete();
  }

  private installTickCallback() {
    const target = window as DtsWindow;
    const original = target.tick_next;
    const callback = (response: unknown) => {
      if (this.xmlImport.consume(response)) return;
      const index = Number(asRecord(response)?.callbackIndex);
      if (!Number.isInteger(index)) return;
      this.completePendingRequest(index, response);
    };
    target.tick_next = callback;
    this.restoreTickCallback = () => {
      if (target.tick_next !== callback) return;
      if (original) target.tick_next = original;
      else delete target.tick_next;
    };
  }
}
