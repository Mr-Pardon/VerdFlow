import { ref, computed } from "vue";
import { type StrategyOption } from "../types";
import type { Ref } from "vue";

export function useStrategyParameters(context: { selectedStrategy: Ref<StrategyOption | null> }) {
  const { selectedStrategy } = context;

  const strategyParameterValues = ref<Record<string, number>>({});

  const randomSeed = ref<number | null>(null);

  const parametersValid = computed(() =>
    (selectedStrategy.value?.generation_parameters ?? []).every((parameter) => {
      const value = strategyParameterValues.value[parameter.key];
      return (
        Number.isFinite(value) &&
        value >= parameter.minimum &&
        value <= parameter.maximum
      );
    }) && (randomSeed.value === null || Number.isSafeInteger(randomSeed.value)),
  );

  const parameterStorageKey = (strategy: StrategyOption) =>
    `verdflow.dts-live.parameters.${strategy.summary.package_hash}`;

  const persistParameterDraft = () => {
    const strategy = selectedStrategy.value;
    if (!strategy) return;
    localStorage.setItem(
      parameterStorageKey(strategy),
      JSON.stringify({
        values: strategyParameterValues.value,
        randomSeed: randomSeed.value,
      }),
    );
  };

  const restoreParameterDraft = (strategy: StrategyOption) => {
    const defaults = Object.fromEntries(
      strategy.generation_parameters.map((parameter) => [
        parameter.key,
        parameter.default,
      ]),
    );
    let values = defaults;
    let seed = strategy.default_random_seed;
    try {
      const raw = localStorage.getItem(parameterStorageKey(strategy));
      const saved = raw ? JSON.parse(raw) as {
        values?: Record<string, unknown>;
        randomSeed?: unknown;
      } : null;
      if (saved?.values) {
        values = Object.fromEntries(
          strategy.generation_parameters.map((parameter) => {
            const value = saved.values?.[parameter.key];
            return [
              parameter.key,
              typeof value === "number" &&
              Number.isFinite(value) &&
              value >= parameter.minimum &&
              value <= parameter.maximum
                ? value
                : parameter.default,
            ];
          }),
        );
      }
      if (saved && (saved.randomSeed === null || Number.isSafeInteger(saved.randomSeed))) {
        seed = saved.randomSeed as number | null;
      }
    } catch {
      localStorage.removeItem(parameterStorageKey(strategy));
    }
    strategyParameterValues.value = values;
    randomSeed.value = seed;
  };

  const updateStrategyParameter = (key: string, value: number) => {
    strategyParameterValues.value = {
      ...strategyParameterValues.value,
      [key]: value,
    };
    persistParameterDraft();
  };

  const updateRandomSeed = (value: number) => {
    randomSeed.value = value;
    persistParameterDraft();
  };

  const rerollRandomSeed = () => {
    const random = new Uint32Array(1);
    crypto.getRandomValues(random);
    randomSeed.value = random[0] ?? 0;
    persistParameterDraft();
  };

  const resetStrategyParameters = () => {
    const strategy = selectedStrategy.value;
    if (!strategy) return;
    strategyParameterValues.value = Object.fromEntries(
      strategy.generation_parameters.map((parameter) => [
        parameter.key,
        parameter.default,
      ]),
    );
    randomSeed.value = strategy.default_random_seed;
    persistParameterDraft();
  };

  return {
    strategyParameterValues,
    randomSeed,
    parametersValid,
    parameterStorageKey,
    persistParameterDraft,
    restoreParameterDraft,
    updateStrategyParameter,
    updateRandomSeed,
    rerollRandomSeed,
    resetStrategyParameters,
  };
}
