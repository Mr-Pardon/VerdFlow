import {
  exampleRoleColor,
  strategyExamplePalette,
  type StrategyExampleTheme,
} from "./strategyExampleTheme";
import type {
  StrategyPreviewResult,
  StrategyPreviewSample,
  PreviewRole,
} from "./types";

export interface StrategyExampleDisplayOptions {
  hiddenRoleKeys?: readonly string[];
  solidPoints?: boolean;
  theme?: StrategyExampleTheme;
}

export function drawStrategyExample(
  canvas: HTMLCanvasElement,
  sample: StrategyPreviewSample,
  result: StrategyPreviewResult | null,
  roles: PreviewRole[],
  referenceRadii: Array<number | null> = [],
  display: StrategyExampleDisplayOptions = {},
) {
  const width = canvas.clientWidth,
    height = canvas.clientHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  if (!ctx || width < 1 || height < 1) return;
  const palette = strategyExamplePalette(display.theme);
  const color = (key: string) => exampleRoleColor(roles, key, display.theme);
  // Rounded backing dimensions can differ from CSS size times a fractional DPR.
  ctx.setTransform(canvas.width / width, 0, 0, canvas.height / height, 0, 0);
  ctx.fillStyle = palette.background;
  ctx.fillRect(0, 0, width, height);
  const isPath = sample.input_geometry === "polyline";
  const outline = isPath ? (sample.path_xy ?? []) : sample.boundary_xy;
  if (outline.length < 2) return;
  const evaluated = isPath ? result?.evaluated_path_xy : null;
  const extent = evaluated ? [...outline, ...evaluated] : outline;
  const xs = extent.map((point) => point[0]),
    ys = extent.map((point) => point[1]);
  const minX = Math.min(...xs),
    maxX = Math.max(...xs),
    minY = Math.min(...ys),
    maxY = Math.max(...ys);
  const scale = Math.min(
    (width - 50) / Math.max(1, maxX - minX),
    (height - 62) / Math.max(1, maxY - minY),
  );
  const left = (width - (maxX - minX) * scale) / 2,
    bottom = (height - (maxY - minY) * scale) / 2;
  const x = (value: number) => left + (value - minX) * scale;
  const y = (value: number) => height - bottom - (value - minY) * scale;
  const desired = 50 / scale;
  const base = 10 ** Math.floor(Math.log10(desired));
  const grid =
    [1, 2, 5, 10]
      .map((value) => value * base)
      .find((value) => value >= desired) ?? base;
  ctx.strokeStyle = palette.grid;
  ctx.lineWidth = 0.6;
  for (
    let value = Math.floor(minX / grid) * grid;
    value <= maxX;
    value += grid
  ) {
    ctx.beginPath();
    ctx.moveTo(x(value), 18);
    ctx.lineTo(x(value), height - 18);
    ctx.stroke();
  }
  for (
    let value = Math.floor(minY / grid) * grid;
    value <= maxY;
    value += grid
  ) {
    ctx.beginPath();
    ctx.moveTo(18, y(value));
    ctx.lineTo(width - 18, y(value));
    ctx.stroke();
  }
  const trace = (points: readonly [number, number][]) => {
    ctx.beginPath();
    points.forEach(([px, py], index) =>
      index ? ctx.lineTo(x(px), y(py)) : ctx.moveTo(x(px), y(py)),
    );
  };
  if (evaluated) {
    ctx.save();
    ctx.globalAlpha = 0.45;
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = palette.boundary;
    ctx.lineWidth = 1;
    trace(outline);
    ctx.stroke();
    ctx.restore();
  }
  trace(evaluated ?? outline);
  if (!isPath) {
    ctx.closePath();
    ctx.fillStyle = palette.region;
    ctx.fill();
  }
  ctx.strokeStyle = palette.boundary;
  ctx.lineWidth = evaluated ? 1.8 : 1.2;
  ctx.stroke();
  if (result) {
    const hidden = new Set(display.hiddenRoleKeys ?? []);
    const layers = result.role_keys
      .map((key, index) => {
        const kind = roles
          .flatMap((role) => role.guide?.diagram ?? [])
          .find((mark) => mark.role_key === key && mark.kind !== "area")?.kind;
        return {
          key,
          index,
          kind,
          order: kind === "tree" ? 2 : kind === "groundcover" ? 0 : 1,
        };
      })
      .filter((layer) => !hidden.has(layer.key))
      .sort((a, b) => a.order - b.order);
    for (const layer of display.solidPoints ? [] : layers) {
      ctx.fillStyle = color(layer.key);
      ctx.strokeStyle = color(layer.key);
      ctx.beginPath();
      result.points.forEach((point, index) => {
        const worldRadius = referenceRadii[index];
        if (point[2] !== layer.index || worldRadius == null) return;
        const radius = worldRadius * scale;
        if (!Number.isFinite(radius) || radius <= 0) return;
        const px = x(point[0]),
          py = y(point[1]);
        ctx.moveTo(px + radius, py);
        ctx.arc(px, py, radius, 0, Math.PI * 2);
      });
      ctx.globalAlpha = palette.rangeFillAlpha;
      ctx.fill();
      ctx.globalAlpha = palette.rangeStrokeAlpha;
      ctx.lineWidth = 0.7;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    for (const layer of layers) {
      const radius =
        layer.kind === "tree" ? 5 : layer.kind === "groundcover" ? 1.65 : 2.6;
      ctx.fillStyle = color(layer.key);
      ctx.globalAlpha = display.solidPoints
        ? 1
        : layer.kind === "tree"
          ? palette.treeAlpha
          : palette.pointAlpha;
      ctx.beginPath();
      for (const [index, point] of result.points.entries()) {
        if (point[2] !== layer.index) continue;
        const px = x(point[0]),
          py = y(point[1]);
        const centerRadius =
          display.solidPoints || referenceRadii[index] == null
            ? radius
            : Math.min(radius, 1.2);
        ctx.moveTo(px + centerRadius, py);
        ctx.arc(px, py, centerRadius, 0, Math.PI * 2);
      }
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  }
  const bar = grid * scale;
  ctx.strokeStyle = palette.scale;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(18, height - 17);
  ctx.lineTo(18 + bar, height - 17);
  ctx.stroke();
  ctx.fillStyle = palette.scale;
  ctx.font = "10px sans-serif";
  ctx.fillText(`${grid} m`, 18, height - 24);
}
