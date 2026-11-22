import {
  INSPECT_LOCAL_ATTACHMENT,
  LOCAL_ATTACHMENT_SCANNER,
  WEB_ATTACHMENT_TIMEOUT_MS,
  type WebAttachmentRequest,
  type WebAttachmentResult,
} from "../web-attachment";

const MAX_QUEUED_SCANS = 3;
let queued = 0;
let scanQueue: Promise<unknown> = Promise.resolve();

async function openScanner(): Promise<void> {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: [chrome.runtime.ContextType.OFFSCREEN_DOCUMENT],
    documentUrls: [chrome.runtime.getURL(LOCAL_ATTACHMENT_SCANNER)],
  });
  if (contexts.length) return;

  await chrome.offscreen.createDocument({
    url: LOCAL_ATTACHMENT_SCANNER,
    reasons: [chrome.offscreen.Reason.WORKERS],
    justification: "Scan website attachments locally in a dedicated worker.",
  });
}

async function scanAttachment(
  message: WebAttachmentRequest,
): Promise<WebAttachmentResult> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await openScanner();
    const scan = chrome.runtime.sendMessage({
      ...message,
      type: INSPECT_LOCAL_ATTACHMENT,
    }) as Promise<WebAttachmentResult | undefined>;
    const timedOut = Symbol("attachment scan timeout");
    const deadline = new Promise<typeof timedOut>((resolve) => {
      timer = setTimeout(() => resolve(timedOut), WEB_ATTACHMENT_TIMEOUT_MS);
    });
    const result = await Promise.race([scan, deadline]);
    if (result === timedOut) {
      await chrome.offscreen.closeDocument().catch(() => {});
      return { status: "unreadable" };
    }
    return result ?? { status: "unreadable" };
  } catch {
    return { status: "unreadable" };
  } finally {
    clearTimeout(timer);
  }
}

export function checkWebAttachment(
  message: WebAttachmentRequest,
): Promise<WebAttachmentResult> {
  // Run one OCR job at a time and cap waiting files across all tabs.
  if (queued >= MAX_QUEUED_SCANS)
    return Promise.resolve({ status: "unreadable" });
  queued += 1;
  const next = scanQueue
    .then(() => scanAttachment(message))
    .finally(() => {
      queued -= 1;
    });
  scanQueue = next;
  return next;
}
