import { overlaps, type Bounds } from "../shared/brushFootprint";

export interface HitBox {
  id: string;
  order: number;
  bounds: [number, number, number, number];
}

interface HitNode {
  bounds: HitBox["bounds"];
  items?: HitBox[];
  children?: [HitNode, HitNode];
}

const contains = ([minX, minY, maxX, maxY]: HitBox["bounds"], x: number, y: number) =>
  x >= minX && x <= maxX && y >= minY && y <= maxY;

function build(items: HitBox[]): HitNode {
  const bounds: HitBox["bounds"] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const item of items) {
    bounds[0] = Math.min(bounds[0], item.bounds[0]);
    bounds[1] = Math.min(bounds[1], item.bounds[1]);
    bounds[2] = Math.max(bounds[2], item.bounds[2]);
    bounds[3] = Math.max(bounds[3], item.bounds[3]);
  }
  if (items.length <= 8) return { bounds, items };
  const axis = bounds[2] - bounds[0] >= bounds[3] - bounds[1] ? 0 : 1;
  const center = (item: HitBox) => item.bounds[axis]! + item.bounds[axis + 2]!;
  items.sort((a, b) => center(a) - center(b));
  const mid = Math.floor(items.length / 2);
  return { bounds, children: [build(items.slice(0, mid)), build(items.slice(mid))] };
}

export class RegionHitIndex {
  private readonly root: HitNode;

  constructor(boxes: HitBox[]) {
    this.root = build(boxes);
  }

  pick(x: number, y: number, hits: (id: string) => boolean): string | undefined {
    let best: HitBox | undefined;
    const visit = (node: HitNode) => {
      if (!contains(node.bounds, x, y)) return;
      for (const item of node.items ?? []) {
        if ((!best || item.order > best.order) && contains(item.bounds, x, y) && hits(item.id)) best = item;
      }
      if (node.children) for (const child of node.children) visit(child);
    };
    visit(this.root);
    return best?.id;
  }

  intersect(bounds: Bounds, hits: (id: string) => boolean): string[] {
    const ids: string[] = [];
    const visit = (node: HitNode) => {
      if (!overlaps(node.bounds, bounds)) return;
      for (const item of node.items ?? []) {
        if (overlaps(item.bounds, bounds) && hits(item.id)) ids.push(item.id);
      }
      if (node.children) for (const child of node.children) visit(child);
    };
    visit(this.root);
    return ids;
  }
}
