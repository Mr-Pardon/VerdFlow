import { onScopeDispose, ref, shallowRef } from "vue";
import { LocalApiClient } from "../../api";
import type { CenterCatalog, CenterDetail } from "../../api/resourceCenter";
import { platformBridge } from "../../platform";

export function useResourceCenter(client = new LocalApiClient(platformBridge())) {
  const catalog = shallowRef<CenterCatalog>();
  const detail = shallowRef<CenterDetail>();
  const loading = ref(false);
  const loadingDetail = ref(false);
  const error = ref("");
  const warning = ref("");
  let disposed = false;
  let listRequest = 0;
  let detailRequest = 0;
  const load = async (refresh = false) => {
    const id = ++listRequest;
    loading.value = true;
    error.value = "";
    try {
      const result = await client.resourceCenter(refresh);
      if (disposed || id !== listRequest) return;
      catalog.value = result;
      warning.value = result.warning;
    } catch (reason) { if (!disposed && id === listRequest) error.value = reason instanceof Error ? reason.message : "资源中心读取失败"; }
    finally { if (!disposed && id === listRequest) loading.value = false; }
  };
  const open = async (resourceId: string, version = "") => {
    const id = ++detailRequest;
    detail.value = undefined;
    error.value = "";
    loadingDetail.value = !!resourceId;
    if (!resourceId) return;
    try {
      const result = await client.resourceCenterDetail(resourceId, version);
      if (disposed || id !== detailRequest) return;
      detail.value = result;
      warning.value = result.warning;
    } catch (reason) { if (!disposed && id === detailRequest) error.value = reason instanceof Error ? reason.message : "资源详情读取失败"; }
    finally { if (!disposed && id === detailRequest) loadingDetail.value = false; }
  };
  onScopeDispose(() => { disposed = true; ++listRequest; ++detailRequest; });
  return { catalog, detail, loading, loadingDetail, error, warning, load, open };
}
