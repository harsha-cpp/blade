import { FONT_UI, palette } from "./theme";

const messages = {
  checking: "Blade is checking the attachment before uploading it.",
  sensitive: "Blade blocked the attachment because it contains sensitive data.",
  unsupported:
    "Blade blocked this file type. Try a PDF, text file, or PNG, JPEG, or WebP image.",
  unreadable:
    "Blade could not finish checking the attachment. Nothing was uploaded. Try again or use an approved system.",
  limit:
    "Blade blocked the upload because it exceeds the attachment scan limits.",
};

export function createAttachmentNotice(documentRef: Document) {
  let dismiss: (() => void) | undefined;
  return (state: keyof typeof messages, identifiers?: string): (() => void) => {
    dismiss?.();
    const colors = palette();
    const host = documentRef.createElement("div");
    host.setAttribute("data-blade-attachment-protection", state);
    host.style.cssText =
      "all:initial;position:fixed;right:24px;bottom:24px;z-index:2147483647";
    const shadow = host.attachShadow({ mode: "closed" });
    const panel = documentRef.createElement("div");
    panel.setAttribute("role", state === "checking" ? "status" : "alert");
    panel.style.cssText = [
      "width:min(380px,calc(100vw - 48px))",
      "box-sizing:border-box;border-radius:6px;padding:14px 16px",
      `background:${colors.surface};color:${colors.ink};border:1px solid ${colors.rule}`,
      `border-top:3px solid ${state === "checking" ? colors.accent : colors.stop}`,
      `box-shadow:${colors.shadow};font:14px/1.45 ${FONT_UI}`,
    ].join(";");
    panel.textContent = identifiers
      ? `${messages[state]} Detected: ${identifiers}.`
      : messages[state];
    shadow.append(panel);
    (documentRef.body ?? documentRef.documentElement).append(host);
    const timer =
      state === "checking" ? undefined : setTimeout(() => host.remove(), 9000);
    const clear = () => {
      clearTimeout(timer);
      host.remove();
      if (dismiss === clear) dismiss = undefined;
    };
    dismiss = clear;
    return clear;
  };
}
