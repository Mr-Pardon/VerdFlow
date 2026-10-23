import { computed, onBeforeUnmount, ref, shallowRef } from "vue";
import { LocalApiClient, LocalApiError } from "../../api";
import type {
  RoleBindingProfileSummary,
  StrategyLibrarySummary,
} from "../../api";
import { platformBridge } from "../../platform";

export const usePlantingPlans = () => {
  const client = new LocalApiClient(platformBridge());
  const plans = shallowRef<RoleBindingProfileSummary[]>([]);
  const strategies = shallowRef<StrategyLibrarySummary[]>([]);
  const loading = ref(false);
  const error = ref("");
  const query = ref("");
  const strategyFilter = ref("");
  let epoch = 0;
  onBeforeUnmount(() => {
    epoch++;
  });
  const refresh = async () => {
    const request = ++epoch;
    loading.value = true;
    error.value = "";
    try {
      const [profiles, installed] = await Promise.all([
        client.roleBindingProfiles({ targetType: "dts" }),
        client.strategies(),
      ]);
      if (request !== epoch) return;
      plans.value = profiles.items;
      strategies.value = installed.items;
    } catch (cause) {
      if (request === epoch)
        error.value =
          cause instanceof LocalApiError ? cause.message : String(cause);
    } finally {
      if (request === epoch) loading.value = false;
    }
  };
  const strategyFor = (plan: RoleBindingProfileSummary) =>
    strategies.value.find(
      (item) =>
        plan.resolved_strategy_hash
          ? item.package_hash === plan.resolved_strategy_hash
          : item.strategy_id === plan.strategy_id &&
            item.strategy_version === plan.strategy_version,
    );
  const strategyNames = computed(() => [
    ...new Map(
      plans.value.map((plan) => [
        plan.strategy_id,
        strategyFor(plan)?.display_name || plan.strategy_id,
      ]),
    ).entries(),
  ]);
  const filtered = computed(() => {
    const needle = query.value.trim().toLocaleLowerCase();
    return plans.value.filter(
      (plan) =>
        (!strategyFilter.value || plan.strategy_id === strategyFilter.value) &&
        (!needle ||
          [
            plan.display_name,
            plan.strategy_id,
            strategyFor(plan)?.display_name ?? "",
            ...(plan.plants ?? []).map((plant) => plant.display_name),
          ].some((value) => value.toLocaleLowerCase().includes(needle))),
    );
  });
  return {
    plans,
    strategies,
    loading,
    error,
    query,
    strategyFilter,
    strategyNames,
    filtered,
    refresh,
    strategyFor,
  };
};
