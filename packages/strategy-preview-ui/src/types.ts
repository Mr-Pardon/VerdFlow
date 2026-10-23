import type { PresentedRole } from "@verdflow/role-guide-ui";

export type PreviewRole = PresentedRole;
export type AssetScalePolicy =
  | { mode: "fixed"; value: number }
  | { mode: "uniform_random"; min: number; max: number };

export interface StrategyPreviewSample {
  display_name: string;
  boundary_xy: [number, number][];
  fixed_seed: number;
}

export interface StrategyPreviewInput {
  package_hash: string;
  strategy_parameters: Record<string, number>;
  excluded_role_keys: string[];
}

export interface StrategyPreviewResult {
  schema: "verdflow.strategy-example-preview";
  version: "1";
  package_hash: string;
  role_keys: string[];
  points: [number, number, number][];
  point_ids?: string[];
}

export interface AssignmentPreviewInput {
  random_seed: number;
  points: [string, string][];
  roles: Array<{
    role_key: string;
    candidates: Array<{
      asset_id: string;
      weight: number;
      scale: AssetScalePolicy;
    }>;
  }>;
}

export interface AssignmentPreviewResult {
  choices: [number, number][];
}

export interface StrategyPreviewOperation {
  preview_id: string;
  package_hash: string;
  status: "pending" | "running" | "succeeded" | "cancelled" | "failed";
  result: StrategyPreviewResult | null;
  error_message: string | null;
}

export interface PreviewStrategy {
  summary: { package_hash: string; executable: boolean };
  roles: PreviewRole[];
  parameter_preview?: StrategyPreviewSample | null;
  generation_parameters: Array<{
    key: string;
    default: number;
    minimum: number;
    maximum: number;
  }>;
}

export interface ExampleAppearanceRole {
  role_key: string;
  candidates: Array<
    AssignmentPreviewInput["roles"][number]["candidates"][number] & {
      reference_radius_m: number | null;
    }
  >;
}

export interface StrategyPreviewClient {
  submitStrategyExample(
    input: StrategyPreviewInput,
  ): Promise<StrategyPreviewOperation>;
  strategyExample(id: string): Promise<StrategyPreviewOperation>;
  cancelStrategyExample(id: string): Promise<StrategyPreviewOperation>;
  previewAssetAssignments(
    input: AssignmentPreviewInput,
  ): Promise<AssignmentPreviewResult>;
}
