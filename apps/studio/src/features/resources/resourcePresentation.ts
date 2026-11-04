import MarkdownIt from "markdown-it";
import type { ResourceKind, ResourceLocalState } from "../../api/resourceCenter";

export const centerUrl = "https://mr-pardon.github.io/verdflow-resources/";
export const kindLabels: Record<ResourceKind, string> = { assets: "资产库", strategies: "种植策略", plans: "种植方案" };
export const stateLabels: Record<ResourceLocalState, string> = {
  available: "未导入", installed: "已导入", local: "本地已有", conflict: "本地内容不同", unavailable: "仅浏览",
};
export const formatBytes = (value: number) => value < 1024 * 1024 ? `${Math.ceil(value / 1024)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`;

export function safeContentImage(source: string, base = centerUrl): string {
  try {
    const url = new URL(source, base);
    return url.href.startsWith(`${centerUrl}data/`) && !url.search && !url.hash
      && /\.(png|jpe?g|webp)$/i.test(url.pathname) ? url.href : "";
  } catch { return ""; }
}

export function renderResourceMarkdown(source: string, base: string): string {
  const md = new MarkdownIt({ html: false, linkify: false, typographer: false });
  md.renderer.rules.image = (tokens, index) => {
    const token = tokens[index]!;
    const url = safeContentImage(String(token.attrGet("src") ?? ""), base);
    return url ? `<img src="${md.utils.escapeHtml(url)}" alt="${md.utils.escapeHtml(token.content)}" loading="lazy" referrerpolicy="no-referrer">` : "";
  };
  md.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
    const token = tokens[index]!;
    let url: URL | undefined;
    try { url = new URL(String(token.attrGet("href") ?? ""), base); } catch { /* Render unsafe links as text. */ }
    if (!url || url.protocol !== "https:" || url.username || url.password) token.attrSet("href", "#");
    else token.attrSet("href", url.href);
    token.attrSet("target", "_blank");
    token.attrSet("rel", "noopener noreferrer");
    return renderer.renderToken(tokens, index, options);
  };
  return md.render(source);
}
