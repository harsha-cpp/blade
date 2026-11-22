import { getDocument, GlobalWorkerOptions } from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { createWorker, OEM, type Worker } from "tesseract.js";
import ocrWorker from "tesseract.js/dist/worker.min.js?url";
import ocrCore from "tesseract.js-core/tesseract-core-lstm.wasm.js?url";
import englishData from "@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz?url";
import { inspectProtectedText } from "../content/input-protection";
import { decodeBase64 } from "../messages";
import {
  WEB_ATTACHMENT_MAX_BYTES,
  type WebAttachmentRequest,
  type WebAttachmentResult,
} from "../web-attachment";

const MAX_PDF_PAGES = 12;
const MAX_RASTER_PIXELS = 24_000_000;
const OCR_EDGE_PIXELS = 2000;
const TEXT_FILENAME = /\.(txt|csv|tsv|log|md|json)$/i;
// Reject binary files masquerading as text, while allowing tabs and line breaks.
// eslint-disable-next-line no-control-regex
const UNSAFE_TEXT_CONTROLS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

type Recognize = (image: HTMLCanvasElement) => Promise<string>;

class ScanLimit extends Error {}

function signature(bytes: Uint8Array, offset: number, value: string): boolean {
  return [...value].every(
    (char, index) => bytes[offset + index] === char.charCodeAt(0),
  );
}

function isAnimatedPng(bytes: Uint8Array): boolean {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let offset = 8; offset + 12 <= bytes.length; ) {
    if (signature(bytes, offset + 4, "acTL")) return true;
    if (signature(bytes, offset + 4, "IDAT")) return false;
    offset += view.getUint32(offset) + 12;
  }
  return false;
}

function imageType(bytes: Uint8Array): string | undefined {
  if (signature(bytes, 0, "\x89PNG\r\n\x1a\n") && !isAnimatedPng(bytes))
    return "image/png";
  if (signature(bytes, 0, "\xff\xd8\xff")) return "image/jpeg";
  if (signature(bytes, 0, "RIFF") && signature(bytes, 8, "WEBP")) {
    const animated =
      signature(bytes, 12, "VP8X") && ((bytes[20] ?? 0) & 2) !== 0;
    if (!animated) return "image/webp";
  }
  return undefined;
}

function canvas(width: number, height: number): HTMLCanvasElement {
  const result = document.createElement("canvas");
  result.width = Math.max(1, Math.ceil(width));
  result.height = Math.max(1, Math.ceil(height));
  return result;
}

async function imageText(blob: Blob, recognize: Recognize): Promise<string> {
  const image = await createImageBitmap(blob);
  try {
    const { width, height } = image;
    if (width * height > MAX_RASTER_PIXELS) throw new ScanLimit();
    const scale = Math.min(1, OCR_EDGE_PIXELS / Math.max(width, height));
    const surface = canvas(width * scale, height * scale);
    const context = surface.getContext("2d", { alpha: false });
    if (!context) throw new Error("Could not render image");
    context.fillStyle = "white";
    context.fillRect(0, 0, surface.width, surface.height);
    context.drawImage(image, 0, 0, surface.width, surface.height);
    return await recognize(surface);
  } finally {
    image.close();
  }
}

function checkText(text: string): WebAttachmentResult {
  const decision = inspectProtectedText(text);
  if (decision.reason === "inspection-limit") return { status: "limit" };
  return decision.blocked
    ? { status: "sensitive", decision }
    : { status: "clean" };
}

async function scanPdf(
  bytes: Uint8Array<ArrayBuffer>,
  recognize: Recognize,
): Promise<WebAttachmentResult> {
  GlobalWorkerOptions.workerSrc = pdfWorker;
  const task = getDocument({
    data: bytes,
    isEvalSupported: false,
    stopAtErrors: true,
  } as Parameters<typeof getDocument>[0]);
  try {
    const pdf = await task.promise;
    if (pdf.numPages > MAX_PDF_PAGES) return { status: "limit" };
    let text = "";
    let unreadable = false;
    for (let i = 1; i <= pdf.numPages; i += 1) {
      const page = await pdf.getPage(i);
      const content = await page.getTextContent();
      const embedded = content.items
        .flatMap((item) => ("str" in item ? [item.str] : []))
        .join(" ");
      text += `\n${embedded}`;
      const embeddedResult = checkText(text);
      if (embeddedResult.status !== "clean") return embeddedResult;

      // The text layer does not contain screenshots or text drawn as paths.
      const original = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({
        scale: OCR_EDGE_PIXELS / Math.max(original.width, original.height),
      });
      const surface = canvas(viewport.width, viewport.height);
      // Print rendering does not wait for animation frames in a hidden document.
      await page.render({ canvas: surface, viewport, intent: "print" }).promise;
      const recognized = await recognize(surface);
      if (!embedded.trim() && !recognized.trim()) unreadable = true;
      text += `\n${recognized}`;
      const renderedResult = checkText(text);
      if (renderedResult.status !== "clean") return renderedResult;
      page.cleanup();
    }
    return { status: unreadable ? "unreadable" : "clean" };
  } finally {
    await task.destroy();
  }
}

function openOcr(): Promise<Worker> {
  const langPath = new URL(".", new URL(englishData, location.href)).href;
  return createWorker("eng", OEM.LSTM_ONLY, {
    workerPath: ocrWorker,
    corePath: ocrCore,
    langPath,
    workerBlobURL: false,
    cacheMethod: "none",
  });
}

export async function scanLocalAttachment(
  request: WebAttachmentRequest,
): Promise<WebAttachmentResult> {
  let worker: Worker | undefined;
  const recognize: Recognize = async (surface) => {
    worker ??= await openOcr();
    const result = await worker.recognize(surface.toDataURL("image/png"));
    return result.data.text;
  };
  try {
    const bytes = decodeBase64(request.data);
    if (bytes.length > WEB_ATTACHMENT_MAX_BYTES) return { status: "limit" };
    if (signature(bytes, 0, "%PDF-")) return await scanPdf(bytes, recognize);
    const mime = imageType(bytes);
    if (mime) {
      const text = await imageText(
        new Blob([bytes], { type: mime }),
        recognize,
      );
      return text.trim() ? checkText(text) : { status: "unreadable" };
    }
    if (!TEXT_FILENAME.test(request.name)) return { status: "unsupported" };
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (UNSAFE_TEXT_CONTROLS.test(text)) return { status: "unreadable" };
    return checkText(text);
  } catch (error) {
    return { status: error instanceof ScanLimit ? "limit" : "unreadable" };
  } finally {
    await worker?.terminate().catch(() => {});
  }
}
