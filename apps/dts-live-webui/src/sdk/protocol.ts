import type { LiveRegion, LivePath } from "../types";

export type UnknownRecord = Record<string, unknown>;

export type DtsApi = UnknownRecord & {
  isCalledInCEF?: boolean;
  callbackIndex?: number;
  settings?: {
    setMousePickMask: (mask: number) => unknown;
  };
  coord?: {
    screen2World?: (x: number, y: number, callback?: (value: unknown) => void) => unknown;
  };
  editHelper?: {
    setParam: (shape: number, mode: number, color: number[]) => void;
    start: () => void;
    cancel?: () => void;
  };
  camera?: {
    lookAtBBox: (bbox: number[], pitch: number, yaw: number, flyTime: number) => unknown;
    get?: (callback?: (value: unknown) => void) => unknown;
    set?: (x: number, y: number, z: number, pitch: number, yaw: number, flyTime: number) => unknown;
    exportOrthoImage?: (path: string, size: number[], width: number, location: number[],
      rotation: number[], color: number[], callback?: (value: unknown) => void) => unknown;
  };
  tileLayer?: {
    getActorInfo?: (data: UnknownRecord, callback?: (value: unknown) => void) => unknown;
    highlightActorsWithColor?: (data: UnknownRecord[], callback?: (value: unknown) => void) => unknown;
    stopHighlightActors?: (data: UnknownRecord, callback?: (value: unknown) => void) => unknown;
  };
  polygon?: {
    add: (data: UnknownRecord | UnknownRecord[]) => unknown;
    update?: (data: UnknownRecord[]) => unknown;
    show: (ids: string | string[]) => unknown;
    hide: (ids: string | string[]) => unknown;
    delete: (id: string) => unknown;
    setColor?: (id: string, color: number[]) => unknown;
  };
  polyline?: {
    add: (data: UnknownRecord) => unknown;
    show: (ids: string | string[]) => unknown;
    hide: (ids: string | string[]) => unknown;
    delete: (id: string) => unknown;
  };
  tools?: {
    linesIntersect: (
      lines: unknown,
      firstOnly: boolean,
      returnDetails: boolean,
      callback?: (value: unknown) => void,
    ) => unknown;
  };
  call?: (document: UnknownRecord) => unknown;
  onConnectionMessage?: (raw: unknown, source?: unknown) => unknown;
};

export interface DtsWindow extends Window {
  DigitalTwinAPI?: new (
    target: null,
    callbacks: {
      onReady: () => void;
      onEvent: (event: unknown) => void;
      onLog: (message: unknown) => void;
    },
  ) => DtsApi;
  fdapi?: { call?: (document: UnknownRecord) => unknown };
  __g?: DtsApi;
  acapi?: { __onCefResponse?: (raw: unknown, source?: unknown) => unknown };
  __onCefResponse?: (raw: unknown, source?: unknown) => unknown;
  ue?: { internal?: unknown };
  tick_next?: (response: unknown) => void;
}

export interface PendingSdkRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timeout: number;
  kind: "sampling" | "actor" | "brush";
}

export interface DtsSdkCallbacks {
  onReady: () => void;
  onActivity: (message: string) => void;
  onError: (message: string) => void;
  onCommandComplete: () => void;
  onRegionDrawn: (region: LiveRegion) => void;
  onPathDrawn?: (path: LivePath) => void;
  onDrawingFinished: () => void;
  onRegionClicked?: (regionId: string) => void;
}
