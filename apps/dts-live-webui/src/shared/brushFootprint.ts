export const REGION_BRUSH_RADIUS = 18;
const SIDES = 8;
const rim = Array.from({ length: SIDES }, (_, index) => {
  const angle = index * Math.PI * 2 / SIDES;
  return [Math.cos(angle) * REGION_BRUSH_RADIUS, Math.sin(angle) * REGION_BRUSH_RADIUS] as const;
});

export function brushScreenSamples(x: number, y: number) {
  return [[x, y] as const, ...rim.map(([dx, dy]) => [x + dx, y + dy] as const)];
}

type Point = readonly number[];
export type Bounds = [number, number, number, number];
type Triangle = { points: [Point, Point, Point]; bounds: Bounds };

const x = (p: Point) => p[0]!;
const y = (p: Point) => p[1]!;
const cross = (a: Point, b: Point, p: Point) => (x(b) - x(a)) * (y(p) - y(a)) - (y(b) - y(a)) * (x(p) - x(a));
const edgeBounds = (a: Point, b: Point): Bounds => [Math.min(x(a), x(b)), Math.min(y(a), y(b)), Math.max(x(a), x(b)), Math.max(y(a), y(b))];
export const overlaps = (a: Bounds, b: Bounds) => a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];

function extent(points: Point[]): Bounds {
  return points.reduce<Bounds>((b, p) => [Math.min(b[0], x(p)), Math.min(b[1], y(p)), Math.max(b[2], x(p)), Math.max(b[3], y(p))],
    [Infinity, Infinity, -Infinity, -Infinity]);
}

export function brushFootprint([center, ...perimeter]: (Point | undefined)[]) {
  const valid = [...(center ? [center] : []), ...perimeter.filter(point => point !== undefined)];
  if (!valid.length) return undefined;

  // An unprojectable ray (e.g. sky) must not create a filled sector.
  const triangles: Triangle[] = [];
  const connected = new Set<number>();
  if (center) {
    for (let i = 0; i < perimeter.length; i++) {
      const j = (i + 1) % perimeter.length;
      const a = perimeter[i], b = perimeter[j];
      if (!a || !b || cross(center, a, b) === 0) continue;
      const points: Triangle["points"] = [center, a, b];
      triangles.push({ points, bounds: extent(points) });
      connected.add(i);
      connected.add(j);
    }
  }
  const probes = [...(center ? [center] : []), ...perimeter.filter((p, i) => p && !connected.has(i))] as Point[];
  return { bounds: extent(valid), triangles, probes };
}

const insideTriangle = (p: Point, [a, b, c]: Triangle["points"]) => {
  const u = cross(a, b, p), v = cross(b, c, p), w = cross(c, a, p);
  return (u >= 0 && v >= 0 && w >= 0) || (u <= 0 && v <= 0 && w <= 0);
};

function crosses(a: Point, b: Point, c: Point, d: Point) {
  if (!overlaps(edgeBounds(a, b), edgeBounds(c, d))) return false;
  const ac = cross(a, b, c), ad = cross(a, b, d), ca = cross(c, d, a), cb = cross(c, d, b);
  return ((ac >= 0 && ad <= 0) || (ad >= 0 && ac <= 0)) && ((ca >= 0 && cb <= 0) || (cb >= 0 && ca <= 0));
}

export function brushIntersectsRings(footprint: NonNullable<ReturnType<typeof brushFootprint>>, rings: readonly Point[][],
  contains: (point: Point) => boolean) {
  if (footprint.probes.some(contains)) return true;
  if (!footprint.triangles.length) return false;
  const [minX, minY, maxX, maxY] = footprint.bounds;
  // Probe containment handles a footprint fully inside the polygon (but not its holes).
  // Checking all outer and inner boundaries also finds narrow or fully enclosed regions.
  for (const ring of rings) {
    let previous = ring.at(-1);
    for (const point of ring) {
      if (previous &&
          !(x(previous) < minX && x(point) < minX) &&
          !(x(previous) > maxX && x(point) > maxX) &&
          !(y(previous) < minY && y(point) < minY) &&
          !(y(previous) > maxY && y(point) > maxY)) {
        const bounds = edgeBounds(previous, point);
        for (const { bounds: sector, points: vertices } of footprint.triangles) {
          if (!overlaps(sector, bounds)) continue;
          if (insideTriangle(point, vertices) ||
              crosses(previous, point, vertices[0], vertices[1]) ||
              crosses(previous, point, vertices[1], vertices[2]) ||
              crosses(previous, point, vertices[2], vertices[0])) return true;
        }
      }
      previous = point;
    }
  }
  return false;
}
