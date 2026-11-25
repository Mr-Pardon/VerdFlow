import { overlaps, type Bounds } from "../shared/brushFootprint";

export interface HitBox {
  id: string;
  order: number;
  bounds: [number, number, number, number];
}

interface HitNode {
  bounds: HitBox["bounds"];
  items?: HitBox[];
  children?: HitNode[];
}

const contains = ([minX, minY, maxX, maxY]: HitBox["bounds"], x: number, y: number) =>
  x >= minX && x <= maxX && y >= minY && y <= maxY;

function extent(items: { bounds: Bounds }[]): Bounds {
  const bounds: HitBox["bounds"] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const item of items) {
    bounds[0] = Math.min(bounds[0], item.bounds[0]);
    bounds[1] = Math.min(bounds[1], item.bounds[1]);
    bounds[2] = Math.max(bounds[2], item.bounds[2]);
    bounds[3] = Math.max(bounds[3], item.bounds[3]);
  }
  return bounds;
}

// Pack X strips into Y-neighbouring groups instead of re-sorting at every binary split.
function pack<T extends { bounds: Bounds }>(items: T[], node: (group: T[]) => HitNode): HitNode[] {
  const capacity = 8;
  if (items.length <= capacity) return [node(items)];
  const stripSize = Math.ceil(Math.sqrt(Math.ceil(items.length / capacity))) * capacity;
  items.sort((a, b) => (a.bounds[0] - b.bounds[0]) + (a.bounds[2] - b.bounds[2]));
  const nodes: HitNode[] = [];
  for (let start = 0; start < items.length; start += stripSize) {
    const strip = items.slice(start, start + stripSize);
    strip.sort((a, b) => (a.bounds[1] - b.bounds[1]) + (a.bounds[3] - b.bounds[3]));
    for (let i = 0; i < strip.length; i += capacity) nodes.push(node(strip.slice(i, i + capacity)));
  }
  return nodes;
}

export class RegionHitIndex {
  private readonly root: HitNode;

  constructor(boxes: HitBox[]) {
    let nodes = pack(boxes, items => ({ items, bounds: extent(items) }));
    while (nodes.length > 1) nodes = pack(nodes, children => ({ children, bounds: extent(children) }));
    this.root = nodes[0]!;
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
