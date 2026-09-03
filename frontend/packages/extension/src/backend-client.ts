import { BACKEND_MESSAGE_GRACE_MS } from "./config";
import type {
  BackendFailureKind,
  BackendRequest,
  BackendResult,
} from "./messages";

export class BackendRequestError extends Error {
  readonly kind: BackendFailureKind;
  readonly status: number | undefined;

  constructor(kind: BackendFailureKind, status?: number, detail?: string) {
    super(detail ?? kind);
    this.name = "BackendRequestError";
    this.kind = kind;
    this.status = status;
  }
}

async function deliver(request: BackendRequest): Promise<BackendResult> {
  try {
    const response = (await chrome.runtime.sendMessage(request)) as
      | BackendResult
      | undefined;
    if (!response) {
      return {
        ok: false,
        kind: "network",
        detail: "no response from the service worker",
      };
    }
    return response;
  } catch (err) {
    return { ok: false, kind: "network", detail: (err as Error).message };
  }
}

export async function sendBackendRequest<T>(
  request: BackendRequest,
  signal?: AbortSignal,
): Promise<T> {
  if (signal?.aborted) {
    throw new BackendRequestError("timeout", undefined, "request aborted");
  }

  let timer: ReturnType<typeof setTimeout> | undefined;
  let onAbort: (() => void) | undefined;
  const deadline = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(
      () =>
        reject(
          new BackendRequestError("timeout", undefined, "request timed out"),
        ),
      request.timeoutMs + BACKEND_MESSAGE_GRACE_MS,
    );
    onAbort = (): void => {
      reject(new BackendRequestError("timeout", undefined, "request aborted"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });

  try {
    const result = await Promise.race([deliver(request), deadline]);
    if (!result.ok) {
      throw new BackendRequestError(result.kind, result.status, result.detail);
    }
    return result.body as T;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    if (onAbort) signal?.removeEventListener("abort", onAbort);
  }
}

export function sendBackendRequestAndForget(request: BackendRequest): void {
  void deliver(request);
}
