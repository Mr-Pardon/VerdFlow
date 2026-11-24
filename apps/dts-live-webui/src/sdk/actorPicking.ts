import type { UnknownRecord } from "./protocol";

export interface ActorPick {
  layer: string;
  actor: string;
  point: number[];
}

export function actorPick(event: UnknownRecord): ActorPick | null {
  const { ObjectID, MouseClickPoint } = event;
  const Id = event.Id ?? event.ID;
  if (event.Type !== "TileLayer" || typeof Id !== "string" || !Id
      || !((typeof ObjectID === "string" && ObjectID.length) || Number.isSafeInteger(ObjectID))
      || !Array.isArray(MouseClickPoint) || MouseClickPoint.length !== 3
      || !MouseClickPoint.every(v => typeof v === "number" && Number.isFinite(v))) return null;
  return { layer: Id, actor: String(ObjectID), point: [...MouseClickPoint] };
}

export function ensureActorPickActive(signal: Pick<AbortSignal, "aborted">) {
  // Explorer's embedded Chromium exposes aborted but not throwIfAborted.
  if (signal.aborted) throw new Error("拾取已取消");
}

