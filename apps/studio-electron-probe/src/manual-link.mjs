export const USER_MANUAL_URL = "https://mr-pardon.github.io/verdflow-site/";
export const RESOURCE_CENTER_URL = "https://mr-pardon.github.io/verdflow-resources/";

const isResourcePage = (value) => {
  try {
    const url = new URL(value);
    return url.origin === "https://mr-pardon.github.io" && url.pathname === "/verdflow-resources/"
      && !url.username && !url.password && !url.hash;
  } catch { return false; }
};

export const manualWindowOpenHandler = ({ openExternal, onError }) => ({ url }) => {
  if (url === USER_MANUAL_URL || isResourcePage(url)) {
    void Promise.resolve()
      .then(() => openExternal(url))
      .catch(onError);
  }
  return { action: "deny" };
};
