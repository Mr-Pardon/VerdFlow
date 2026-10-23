import type { StrategyGenerationParameterInfo } from "../../api";

export const visibleParameters = (
  parameters: StrategyGenerationParameterInfo[],
  enabled: Record<string, boolean>,
): StrategyGenerationParameterInfo[] => parameters.filter(parameter =>
  !parameter.visible_for_roles?.length || parameter.visible_for_roles.some(roleKey => enabled[roleKey] !== false),
);

export const parameterPresetValues = (
  parameters: StrategyGenerationParameterInfo[],
  preset: Record<string, number> = {},
): Record<string, number> => Object.fromEntries(
  parameters.map((parameter) => [parameter.key, preset[parameter.key] ?? parameter.default]),
);

export const parameterValuesValid = (
  parameters: StrategyGenerationParameterInfo[],
  values: Record<string, number>,
): boolean => parameters.every((parameter) => {
  const value = values[parameter.key];
  return Number.isFinite(value) && value >= parameter.minimum && value <= parameter.maximum;
});
