import { encodeBase64 } from "../messages";
import {
  CHECK_WEB_ATTACHMENT,
  WEB_ATTACHMENT_BATCH_LIMIT,
  WEB_ATTACHMENT_BATCH_MAX_BYTES,
  WEB_ATTACHMENT_MAX_BYTES,
  type WebAttachmentResult,
} from "../web-attachment";
import {
  inspectProtectedText,
  type InputProtectionDecision,
} from "./input-protection";
import { createAttachmentNotice } from "./web-attachment-notice";

async function checkFile(file: File): Promise<WebAttachmentResult> {
  const data = encodeBase64(new Uint8Array(await file.arrayBuffer()));
  const result = (await chrome.runtime.sendMessage({
    type: CHECK_WEB_ATTACHMENT,
    name: file.name,
    data,
  })) as WebAttachmentResult | undefined;
  return result ?? { status: "unreadable" };
}

function hold(event: Event): void {
  event.preventDefault();
  event.stopImmediatePropagation();
}

export function installWebAttachmentProtection(
  onBlocked: (decision: InputProtectionDecision) => void,
): void {
  const notice = createAttachmentNotice(document);
  const verifiedEvents = new WeakSet<Event>();
  const pendingInputs = new WeakMap<HTMLInputElement, object>();

  function replay(target: EventTarget, event: Event): boolean {
    verifiedEvents.add(event);
    return target.dispatchEvent(event);
  }

  function setVerifiedSelection(
    input: HTMLInputElement,
    transfer: DataTransfer,
  ): void {
    input.files = transfer.files;
    replay(input, new Event("input", { bubbles: true, composed: true }));
    replay(input, new Event("change", { bubbles: true }));
  }

  async function inspect(
    files: File[],
    event: Event,
    stillCurrent: () => boolean,
    release: () => void,
  ): Promise<void> {
    const limitExceeded =
      files.length > WEB_ATTACHMENT_BATCH_LIMIT ||
      files.some((file) => file.size > WEB_ATTACHMENT_MAX_BYTES) ||
      files.reduce((total, file) => total + file.size, 0) >
        WEB_ATTACHMENT_BATCH_MAX_BYTES;
    if (limitExceeded) {
      notice("limit");
      return;
    }

    const clearProgress = notice("checking");
    try {
      for (const file of files) {
        const result = await checkFile(file);
        if (!stillCurrent()) return;
        if (result.status === "clean") continue;
        if (result.status === "sensitive") {
          notice("sensitive", result.decision.labels.join(", "));
          if (event.isTrusted) onBlocked(result.decision);
        } else {
          notice(result.status);
        }
        return;
      }
      release();
    } catch {
      if (stillCurrent()) notice("unreadable");
    } finally {
      clearProgress();
    }
  }

  function onFileInput(event: Event): void {
    if (verifiedEvents.has(event)) return;
    const input = event
      .composedPath()
      .find(
        (item): item is HTMLInputElement =>
          item instanceof HTMLInputElement && item.type === "file",
      );
    if (!input) return;
    const files = Array.from(input.files ?? []);
    if (!files.length) {
      if (pendingInputs.has(input)) hold(event);
      return;
    }
    // File pickers fire input before change. Clear the selection before either
    // event reaches a site that starts uploading as soon as a file is picked.
    hold(event);
    input.value = "";
    const attempt = {};
    pendingInputs.set(input, attempt);
    const isCurrent = () =>
      pendingInputs.get(input) === attempt && input.isConnected;
    void inspect(files, event, isCurrent, () => {
      const transfer = new DataTransfer();
      for (const file of files) transfer.items.add(file);
      setVerifiedSelection(input, transfer);
    }).finally(() => {
      if (pendingInputs.get(input) === attempt) pendingInputs.delete(input);
    });
  }

  function onTransfer(event: ClipboardEvent | DragEvent): void {
    if (verifiedEvents.has(event)) return;
    const data =
      "clipboardData" in event ? event.clipboardData : event.dataTransfer;
    const files = Array.from(data?.files ?? []);
    if (!files.length) {
      if (Array.from(data?.items ?? []).some((item) => item.kind === "file")) {
        hold(event);
        notice("unsupported");
      }
      return;
    }
    hold(event);

    const text = inspectProtectedText(data?.getData("text/plain") ?? "");
    if (text.blocked) {
      notice(
        text.reason === "inspection-limit" ? "limit" : "sensitive",
        text.labels.join(", "),
      );
      if (event.isTrusted) onBlocked(text);
      return;
    }
    const target = event.composedPath()[0];
    if (!(target instanceof Node)) return;
    void inspect(
      files,
      event,
      () => target.isConnected,
      () => {
        // Rebuild the transfer from the checked files. Clipboard HTML, URLs, and
        // other transfer payloads are not part of the file scan.
        const verified = new DataTransfer();
        for (const file of files) verified.items.add(file);
        const options = { bubbles: true, cancelable: true, composed: true };
        const replacement =
          "clipboardData" in event
            ? new ClipboardEvent("paste", {
                ...options,
                clipboardData: verified,
              })
            : new DragEvent("drop", {
                ...options,
                dataTransfer: verified,
                clientX: event.clientX,
                clientY: event.clientY,
              });
        if (
          replay(target, replacement) &&
          event.type === "drop" &&
          target instanceof HTMLInputElement &&
          target.type === "file" &&
          !target.disabled &&
          (target.multiple || verified.files.length === 1)
        ) {
          // Synthetic drops do not run the browser's native file-input action.
          setVerifiedSelection(target, verified);
        }
      },
    );
  }

  window.addEventListener("input", onFileInput, true);
  window.addEventListener("change", onFileInput, true);
  window.addEventListener("paste", onTransfer, true);
  window.addEventListener("drop", onTransfer, true);
}
