import type { Bounds3D } from "../../api";

export type RegionBoundary = Array<Array<[number, number, number]>>;

export const boundsContainPoint = (
  bounds: Bounds3D,
  x: number,
  y: number,
) =>
  x >= bounds.min_x &&
  x <= bounds.max_x &&
  y >= bounds.min_y &&
  y <= bounds.max_y;

export const pointInBoundary = (
  rings: RegionBoundary,
  x: number,
  y: number,
) => {
  let inside = false;
  for (const ring of rings) {
    if (ring.length < 3) continue;
    for (
      let current = 0, previous = ring.length - 1;
      current < ring.length;
      previous = current, current += 1
    ) {
      const [currentX, currentY] = ring[current];
      const [previousX, previousY] = ring[previous];
      if (
        currentY > y !== previousY > y &&
        x <
          ((previousX - currentX) * (y - currentY)) /
            (previousY - currentY) +
            currentX
      ) {
        inside = !inside;
      }
    }
  }
  return inside;
};

export const boundaryIntersectsRectangle = (
  rings: RegionBoundary,
  minX: number,
  minY: number,
  maxX: number,
  maxY: number,
) => {
  const corners: Array<[number, number]> = [
    [minX, minY],
    [maxX, minY],
    [maxX, maxY],
    [minX, maxY],
  ];
  if (corners.some(([x, y]) => pointInBoundary(rings, x, y))) return true;
  const rectangleEdges = corners.map(
    (point, index) =>
      [point, corners[(index + 1) % corners.length]] as const,
  );
  for (const ring of rings) {
    for (let index = 0; index < ring.length; index += 1) {
      const first = ring[index];
      const second = ring[(index + 1) % ring.length];
      if (
        (first[0] >= minX &&
          first[0] <= maxX &&
          first[1] >= minY &&
          first[1] <= maxY) ||
        rectangleEdges.some(([start, end]) =>
          segmentsIntersect(
            [first[0], first[1]],
            [second[0], second[1]],
            start,
            end,
          ),
        )
      ) {
        return true;
      }
    }
  }
  return false;
};

const segmentsIntersect = (
  firstStart: [number, number],
  firstEnd: [number, number],
  secondStart: [number, number],
  secondEnd: [number, number],
) => {
  const cross = (
    origin: [number, number],
    first: [number, number],
    second: [number, number],
  ) =>
    (first[0] - origin[0]) * (second[1] - origin[1]) -
    (first[1] - origin[1]) * (second[0] - origin[0]);
  const sign = (value: number) =>
    Math.abs(value) < 1e-9 ? 0 : Math.sign(value);
  const onSegment = (
    start: [number, number],
    point: [number, number],
    end: [number, number],
  ) =>
    point[0] >= Math.min(start[0], end[0]) - 1e-9 &&
    point[0] <= Math.max(start[0], end[0]) + 1e-9 &&
    point[1] >= Math.min(start[1], end[1]) - 1e-9 &&
    point[1] <= Math.max(start[1], end[1]) + 1e-9;
  const sides = [
    cross(firstStart, firstEnd, secondStart),
    cross(firstStart, firstEnd, secondEnd),
    cross(secondStart, secondEnd, firstStart),
    cross(secondStart, secondEnd, firstEnd),
  ];
  const signs = sides.map(sign);
  if (signs[0] * signs[1] < 0 && signs[2] * signs[3] < 0) return true;
  return (
    (signs[0] === 0 && onSegment(firstStart, secondStart, firstEnd)) ||
    (signs[1] === 0 && onSegment(firstStart, secondEnd, firstEnd)) ||
    (signs[2] === 0 && onSegment(secondStart, firstStart, secondEnd)) ||
    (signs[3] === 0 && onSegment(secondStart, firstEnd, secondEnd))
  );
};
