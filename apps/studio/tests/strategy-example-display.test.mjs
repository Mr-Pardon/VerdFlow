import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import vue from "@vitejs/plugin-vue";
import { createServer } from "vite";

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  configFile: false,
  plugins: [vue()],
  cacheDir: fileURLToPath(
    new URL(
      "../../../tests/.tmp/strategy-example-display-vite",
      import.meta.url,
    ),
  ),
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true, hmr: false, ws: false },
});
globalThis.window = { devicePixelRatio: 1 };
const { drawStrategyExample, exampleRoleColor } = await server.ssrLoadModule(
  "@verdflow/strategy-preview-ui",
);
const sample = {
  boundary_xy: [
    [0, 0],
    [100, 0],
    [100, 100],
    [0, 100],
  ],
};
const result = {
  role_keys: ["ground", "tree"],
  points: [
    [10, 10, 0],
    [20, 20, 0],
    [50, 50, 1],
  ],
};
const roles = [
  {
    role_key: "ground",
    guide: { diagram: [{ role_key: "ground", kind: "groundcover" }] },
  },
  {
    role_key: "tree",
    guide: { diagram: [{ role_key: "tree", kind: "tree" }] },
  },
];

function drawFrame(
  radii = [10, 10, 10],
  display = {},
  size = { width: 400, height: 400 },
) {
  let arcs = [];
  let background;
  let transform;
  let paintedSize;
  const fills = [];
  const context = {
    globalAlpha: 1,
    setTransform(...values) {
      transform = values;
    },
    fillRect(x, y, width, height) {
      background = this.fillStyle;
      paintedSize = [width, height];
    },
    moveTo() {},
    lineTo() {},
    closePath() {},
    stroke() {},
    fillText() {},
    beginPath() {
      arcs = [];
    },
    arc(x, y, radius) {
      arcs.push({ x, y, radius });
    },
    fill() {
      fills.push({
        alpha: this.globalAlpha,
        color: this.fillStyle,
        arcs: [...arcs],
      });
    },
  };
  const canvas = {
    clientWidth: size.width,
    clientHeight: size.height,
    getContext: () => context,
  };
  drawStrategyExample(canvas, sample, result, roles, radii, display);
  return {
    background,
    transform,
    paintedSize,
    backingSize: [canvas.width, canvas.height],
    fills: fills.filter((fill) => fill.arcs.length > 0),
  };
}
const draw = (...args) => drawFrame(...args).fills;

try {
  await test("solid display uses opaque centers instead of translucent reference circles", () => {
    const reference = draw();
    const solid = draw(undefined, { solidPoints: true });
    assert.equal(reference.flatMap((fill) => fill.arcs).length, 6);
    assert.equal(solid.flatMap((fill) => fill.arcs).length, 3);
    assert.ok(solid.every((fill) => fill.alpha === 1));
    assert.ok(
      solid.flatMap((fill) => fill.arcs).every((arc) => arc.radius <= 5),
    );
    assert.ok(
      reference.flatMap((fill) => fill.arcs).some((arc) => arc.radius > 5),
    );
  });
  await test("legend visibility hides both ranges and centers without changing source points", () => {
    const original = structuredClone(result);
    for (const solidPoints of [false, true]) {
      const visible = draw(undefined, {
        hiddenRoleKeys: ["tree"],
        solidPoints,
      });
      assert.equal(
        visible.flatMap((fill) => fill.arcs).length,
        solidPoints ? 2 : 4,
      );
      assert.deepEqual(
        draw(undefined, { hiddenRoleKeys: ["ground", "tree"], solidPoints }),
        [],
      );
    }
    assert.deepEqual(result, original);
  });
  await test("missing reference radii retain the current symbols and support solid mode", () => {
    const normal = draw([]);
    assert.equal(normal.flatMap((fill) => fill.arcs).length, 3);
    assert.ok(normal.every((fill) => fill.alpha < 1));
    const solid = draw([], { solidPoints: true });
    assert.equal(solid.flatMap((fill) => fill.arcs).length, 3);
    assert.ok(solid.every((fill) => fill.alpha === 1));
  });
  await test("dark theme adapts canvas and role contrast without changing geometry or source data", () => {
    const original = structuredClone(result);
    const light = drawFrame();
    const dark = drawFrame(undefined, { theme: "dark" });
    assert.equal(light.background, "#f7faf8");
    assert.equal(dark.background, "#101f1d");
    assert.deepEqual(
      dark.fills.map((fill) => fill.arcs),
      light.fills.map((fill) => fill.arcs),
    );
    for (const [index, role] of roles.entries()) {
      const expected = exampleRoleColor(roles, role.role_key, "dark");
      assert.equal(dark.fills[index].color, expected);
      assert.notEqual(expected, exampleRoleColor(roles, role.role_key));
    }
    const solid = draw([], { theme: "dark", solidPoints: true });
    assert.equal(solid.flatMap((fill) => fill.arcs).length, 3);
    assert.ok(solid.every((fill) => fill.alpha === 1));
    assert.deepEqual(result, original);
  });
  await test("fractional DPR fills the rounded backing canvas without changing logical geometry", () => {
    try {
      const size = { width: 401, height: 263 };
      const original = drawFrame(undefined, {}, size).fills;
      for (const dpr of [1, 1.25, 1.5, 2, 3]) {
        globalThis.window.devicePixelRatio = dpr;
        const frame = drawFrame(undefined, {}, size);
        const capped = Math.min(dpr, 2);
        assert.deepEqual(frame.backingSize, [
          Math.round(size.width * capped),
          Math.round(size.height * capped),
        ]);
        const [scaleX, skewX, skewY, scaleY, offsetX, offsetY] = frame.transform;
        assert.deepEqual([skewX, skewY, offsetX, offsetY], [0, 0, 0, 0]);
        assert.ok(Math.abs(frame.paintedSize[0] * scaleX - frame.backingSize[0]) < 1e-9);
        assert.ok(Math.abs(frame.paintedSize[1] * scaleY - frame.backingSize[1]) < 1e-9);
        assert.deepEqual(frame.fills, original);
      }
    } finally {
      globalThis.window.devicePixelRatio = 1;
    }
  });
} finally {
  await server.close();
}
