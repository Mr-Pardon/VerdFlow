import type { LocalApiClient } from "../../api";

export async function hasExistingSetup(client: LocalApiClient, hasProjects: () => boolean): Promise<boolean> {
  if (hasProjects()) return true;
  const [dts, blender, strategies, models, runtimes] = await Promise.all([
    client.assetCatalogs("dts"), client.assetCatalogs("blender"), client.strategies(),
    client.modelProviders(), client.externalRuntimes(),
  ]);
  return hasProjects() || dts.items.length > 0 || blender.items.length > 0 || strategies.items.length > 0
    || models.items.some(item => item.secret_configured || !item.builtin)
    || runtimes.items.some(item => Boolean(item.configured_path));
}
