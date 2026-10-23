import { cancelOperation, submitDelivery, submitElevation, submitElevationChunk, submitSelection } from "../api";
import type { ElevationBatch, LiveCommand, LiveRegion } from "../types";
import type { DtsApi, DtsWindow, PendingSdkRequest, DtsSdkCallbacks } from "./protocol";
import { loadScript, settle, isPromise, asRecord, readableError } from "./runtime";
import { findCoordinates, createLiveRegion, previewFrameThickness } from "./regions";
import { samplingWindows, intersectionRows } from "./elevation";
import { DtsXmlImport, XmlImportCancelled } from "./xmlImport";
import { readCameraPose, type CameraPose } from "./camera";

const CAMERA_FLIGHT_SECONDS = 0.8;

export class DtsSdkBridge {
  private disposed = false;
  private api: DtsApi | null = null;
  private selection: LiveCommand | null = null;
  private draftDrawing = false;
  private drawingNumber = 0;
  private samplingGeneration = 0;
  private mousePickingConfigured = false;
  private readonly previewGroup = `verdflow_selection_${crypto.randomUUID()}`;
  private readonly previewIds = new Map<string, string>();
  private readonly previewRegions = new Map<string, string>();
  private readonly previewShapes = new Map<string, { region: LiveRegion; path?: Path2D }>();
  private pickContext: CanvasRenderingContext2D | null = null;
  private readonly pendingRequests = new Map<number, PendingSdkRequest>();
  private readonly xmlImport: DtsXmlImport;
  private savedCamera: { generation: number; pose: CameraPose } | null = null;
  private readonly sceneWaits = new Map<number, () => void>();
  private restoreTickCallback: (() => void) | null = null;

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
        const result = this.api.settings.setMousePickMask(1);
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
    this.cancelDrawing();
    this.cancelSampling();
    this.clearRegionPreviews();
    this.xmlImport.dispose();
    this.restoreTickCallback?.();
    this.restoreTickCallback = null;
    this.pickContext = null;
  }

  beginDrawing() {
    if (!this.api?.editHelper) throw new Error("DTS 选面工具不可用");
    if (this.draftDrawing) return;
    this.draftDrawing = true;
    try {
      this.api.editHelper.setParam(0, 1, [53, 183, 159, 255]);
      this.api.editHelper.start();
    } catch (error) {
      this.draftDrawing = false;
      throw error;
    }
  }

  cancelDrawing() {
    const wasDrawing = this.draftDrawing || this.selection !== null;
    this.draftDrawing = false;
    this.selection = null;
    if (wasDrawing) this.api?.editHelper?.cancel?.();
    this.callbacks.onDrawingFinished();
  }

  showRegion(region: LiveRegion) {
    if (!this.api?.polygon) throw new Error("DTS 选区预览接口不可用");
    if (this.previewIds.has(region.region_id)) return;
    const id = `${this.previewGroup}_${region.region_id}`;
    this.api.polygon.add({
      id, groupId: this.previewGroup, coordinates: region.coordinates,
      coordinateType: 0, style: 10, color: [0.14, 0.63, 0.59, 0.3],
      frameColor: [0.36, 0.95, 0.82, 0.95], frameThickness: previewFrameThickness(region),
      range: [1, 1000000], depthTest: false, priority: 1,
    });
    this.previewIds.set(region.region_id, id);
    this.previewRegions.set(id, region.region_id);
    this.previewShapes.set(region.region_id, { region });
  }

  hideRegion(regionId: string) {
    const id = this.previewIds.get(regionId);
    if (!id) return;
    this.api?.polygon?.delete(id);
    this.previewIds.delete(regionId);
    this.previewRegions.delete(id);
    this.previewShapes.delete(regionId);
  }

  clearRegionPreviews() {
    for (const regionId of [...this.previewIds.keys()]) {
      try { this.hideRegion(regionId); }
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
    for (const index of [...this.pendingRequests.keys()]) {
      this.failPendingRequest(index, "高程采样已取消");
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
    if (document?.eventtype === "LeftMouseButtonClick") {
      let regionId = document.Type === "Polygon" && typeof document.Id === "string"
        ? this.previewRegions.get(document.Id) : undefined;
      // OnTerrain polygons may be picked as the underlying terrain in Explorer.
      // Never reinterpret a click on somebody else's Polygon as our selection.
      if (!regionId && document.Type === "TileLayer") {
        regionId = this.regionAtTerrainPoint(document.MouseClickPoint);
      }
      if (regionId) this.callbacks.onRegionClicked?.(regionId);
      return;
    }
    if (document?.eventtype === "EditHelperFinished" && this.draftDrawing) {
      this.draftDrawing = false;
      this.callbacks.onDrawingFinished();
      try {
        const coordinates = findCoordinates(event);
        if (!coordinates) throw new Error("DTS 选面结果没有可识别的坐标");
        const region = createLiveRegion(coordinates, ++this.drawingNumber);
        this.callbacks.onRegionDrawn(region);
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

  private regionAtTerrainPoint(value: unknown): string | undefined {
    if (!Array.isArray(value) || value.length !== 3 || !value.every(v => typeof v === "number" && Number.isFinite(v))) return;
    const [x, y] = value as [number, number, number];
    for (const [regionId, shape] of [...this.previewShapes].reverse()) {
      const { region } = shape;
      const [minX, minY, , maxX, maxY] = region.bbox as [number, number, number, number, number, number];
      if (x < minX || x > maxX || y < minY || y > maxY) continue;
      this.pickContext ??= document.createElement("canvas").getContext("2d");
      if (!this.pickContext) return;
      if (!shape.path) {
        const path = new Path2D();
        region.coordinates.forEach((point, index) => {
          if (index === 0) path.moveTo(point[0]! - minX, point[1]! - minY);
          else path.lineTo(point[0]! - minX, point[1]! - minY);
        });
        path.closePath();
        shape.path = path;
      }
      if (this.pickContext.isPointInPath(shape.path, x - minX, y - minY, "evenodd")) return regionId;
    }
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
    const lines = command.payload.lines;
    if (!this.api?.tools?.linesIntersect || !Array.isArray(lines)) {
      throw new Error("DTS 高程采样工具不可用");
    }
    const batches = command.payload.batches as ElevationBatch[] | undefined;
    const plan = batches?.length ? batches : [{
      region_id: "selection", name: "选区", bbox: [], start: 0, count: lines.length,
    }];
    const streamed = command.payload.stream_results === true;
    const results: unknown[] = streamed ? [] : Array(lines.length).fill(null);
    const failures = new Map<string, string>();
    const assertActive = () => {
      if (generation !== this.samplingGeneration) throw new Error("高程采样已取消");
    };
    const windows = samplingWindows(lines, plan);
    for (const [index, window] of windows.entries()) {
      assertActive();
      if (window.groups.every(group => failures.has(group.region_id))) continue;
      this.callbacks.onActivity(`定位采样窗口 ${index + 1}/${windows.length}`);
      this.focusRegion(window);
      await this.waitForScene(1400);
      assertActive();
      for (const group of window.groups) {
        if (failures.has(group.region_id)) continue;
        for (let offset = 0; offset < group.indices.length; offset += 2000) {
          const indices = group.indices.slice(offset, offset + 2000);
          const chunk = indices.map(i => lines[i]);
          let rows: unknown[] | null = null;
          for (let attempt = 0; attempt < 2; attempt++) {
            assertActive();
            this.callbacks.onActivity(`采样窗口 ${index + 1}/${windows.length} · ${group.name}${attempt ? " · 重试" : ""}`);
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
              if (attempt === 1) failures.set(group.region_id, readableError(error));
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
    await submitElevation(command.correlation, streamed ? null : results,
      [...failures].map(([region_id, reason]) => ({ region_id, reason })), streamed);
    this.callbacks.onActivity(failures.size ? `采样完成，跳过 ${failures.size} 个失败面` : "采样完成，正在整理高程结果并生成 XML");
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
  ): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const index = Number(this.api?.callbackIndex ?? 0) + 1;
      const timeout = window.setTimeout(() => {
        this.failPendingRequest(index, message);
      }, milliseconds);
      this.pendingRequests.set(index, { resolve, reject, timeout });
      try {
        const callback = (value: unknown) => { this.completePendingRequest(index, value); };
        const result = invoke(this.api?.isCalledInCEF ? undefined : callback);
        if (isPromise(result)) {
          void result.then(value => {
            if (value != null) callback(value);
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
