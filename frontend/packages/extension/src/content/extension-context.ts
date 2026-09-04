import { FONT_UI, palette } from "./theme";

const BANNER_ID = "blade-context-banner";
const CONTEXT_INVALIDATED = /extension context invalidated/i;

export const CONTEXT_INVALIDATED_TEXT =
  "Blade was updated and stopped checking this tab. Reload Gmail to turn protection back on.";

export function isExtensionContextValid(): boolean {
  try {
    return typeof chrome !== "undefined" && Boolean(chrome.runtime?.id);
  } catch {
    return false;
  }
}

export function isContextInvalidatedError(err: unknown): boolean {
  const message =
    err instanceof Error ? err.message : typeof err === "string" ? err : "";
  return CONTEXT_INVALIDATED.test(message);
}

export function showContextInvalidatedBanner(doc: Document): void {
  if (doc.getElementById(BANNER_ID)) return;
  const p = palette();

  const banner = doc.createElement("div");
  banner.id = BANNER_ID;
  banner.setAttribute("role", "alert");
  banner.style.cssText = `position:fixed;top:0;left:0;right:0;z-index:2147483646;box-sizing:border-box;padding:8px 14px;background:${p.surface};color:${p.ink};border-bottom:2px solid ${p.stop};font-family:${FONT_UI};font-size:12.5px;line-height:1.4;text-align:center;`;
  banner.textContent = CONTEXT_INVALIDATED_TEXT;

  (doc.body ?? doc.documentElement).appendChild(banner);
}
