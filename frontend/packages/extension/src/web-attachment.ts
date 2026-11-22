import type { InputProtectionDecision } from "./content/input-protection";

export const CHECK_WEB_ATTACHMENT = "CHECK_WEB_ATTACHMENT";
export const INSPECT_LOCAL_ATTACHMENT = "INSPECT_LOCAL_ATTACHMENT";
export const LOCAL_ATTACHMENT_SCANNER = "src/offscreen/index.html";
export const WEB_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
export const WEB_ATTACHMENT_BATCH_LIMIT = 8;
export const WEB_ATTACHMENT_BATCH_MAX_BYTES = 20 * 1024 * 1024;
export const WEB_ATTACHMENT_TIMEOUT_MS = 20_000;

export type WebAttachmentResult =
  | { status: "clean" }
  | { status: "sensitive"; decision: InputProtectionDecision }
  | { status: "unsupported" | "unreadable" | "limit" };

export interface WebAttachmentRequest {
  type: typeof CHECK_WEB_ATTACHMENT | typeof INSPECT_LOCAL_ATTACHMENT;
  name: string;
  data: string;
}

export function hasAttachmentPayload(
  value: Partial<WebAttachmentRequest>,
): value is WebAttachmentRequest {
  return (
    typeof value.name === "string" &&
    value.name.length <= 255 &&
    typeof value.data === "string" &&
    value.data.length <= 4 * Math.ceil(WEB_ATTACHMENT_MAX_BYTES / 3)
  );
}
