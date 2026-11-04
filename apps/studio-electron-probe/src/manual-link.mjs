export const USER_MANUAL_URL = "https://mr-pardon.github.io/verdflow-site/";
export const RESOURCE_CENTER_URL = "https://mr-pardon.github.io/verdflow-resources/";

const isResourcePage = (value) => {
  try {
    const url = new URL(value);
    return url.origin === "https://mr-pardon.github.io" && url.pathname === "/verdflow-resources/"
      && !url.username && !url.password && !url.hash;
  } catch { return false; }
};

const isResourceSupportingLink = (value) => {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return false;
    if (url.origin === "https://mr-pardon.github.io" && url.pathname.startsWith("/verdflow-site/") && !url.search) return true;
    // The current reviewed PAK resources use Baidu share links, not arbitrary browser destinations.
    return url.hostname === "pan.baidu.com" && /^\/s\/[A-Za-z0-9_-]+$/.test(url.pathname)
      && [...url.searchParams.keys()].every(key => key === "pwd") && !url.hash;
  } catch { return false; }
};

export const manualWindowOpenHandler = ({ openExternal, onError }) => ({ url }) => {
  if (url === USER_MANUAL_URL || isResourcePage(url) || isResourceSupportingLink(url)) {
    void Promise.resolve()
      .then(() => openExternal(url))
      .catch(onError);
  }
  return { action: "deny" };
};
