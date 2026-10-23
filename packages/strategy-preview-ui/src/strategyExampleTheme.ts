import { roleColor } from "@verdflow/role-guide-ui";
import type { PreviewRole } from "./types";

export type StrategyExampleTheme = "light" | "dark";

const PALETTES = {
  light: {
    background: "#f7faf8",
    grid: "#e8efeb",
    region: "#e7f0e8",
    boundary: "#a2bcae",
    scale: "#728d80",
    overlay: "#f7faf8d9",
    switchTrack: "#d5dfda",
    rangeFillAlpha: 0.14,
    rangeStrokeAlpha: 0.28,
    treeAlpha: 0.78,
    pointAlpha: 0.58,
  },
  dark: {
    background: "#101f1d",
    grid: "#233a34",
    region: "#1c3831",
    boundary: "#739e8b",
    scale: "#a4bdb1",
    overlay: "#101f1de6",
    switchTrack: "#304a41",
    rangeFillAlpha: 0.16,
    rangeStrokeAlpha: 0.45,
    treeAlpha: 0.95,
    pointAlpha: 0.85,
  },
};

export const strategyExamplePalette = (theme: StrategyExampleTheme = "light") =>
  PALETTES[theme];

export function exampleRoleColor(
  roles: PreviewRole[],
  key: string,
  theme: StrategyExampleTheme = "light",
) {
  const color = roleColor(roles, key);
  if (theme === "light") return color;
  // Keep the shared role hues, lifting their contrast against a dark plot.
  const channels = [1, 3, 5].map((offset) =>
    Math.round(parseInt(color.slice(offset, offset + 2), 16) * 0.6 + 255 * 0.4),
  );
  return `rgb(${channels.join(", ")})`;
}
