import {
  BACKEND_MESSAGE_GRACE_MS,
  BACKEND_RETRY_AFTER_FALLBACK_MS,
} from "./config";
import type {
  BackendFailureKind,
  BackendRequest,
  BackendResult,
} from "./messages";

export class BackendRequestError extends Error {
  readonly kind: BackendFailureKind;
  readonly status: number | undefined;
  readonly retryAfterMs: number | undefined;

  constructor(
    kind: BackendFailureKind,
    status?: number,
    detail?: string,
    retryAfterMs?: number,
  ) {
    super(detail ?? kind);
    this.name = "BackendRequestError";
    this.kind = kind;
    this.status = status;
    this.retryAfterMs = retryAfterMs;
  }
}

export function isNotLinked(err: unknown): boolean {
  return (
    err instanceof BackendRequestError &&
    (err.status === 401 || err.status === 403)
  );
}

export function isAttachmentRejected(err: unknown): boolean {
  return err instanceof BackendRequestError && err.status === 413;
}

export function retryDelayMs(err: unknown): number | null {
  if (!(err instanceof BackendRequestError) || err.status !== 429) return null;
  return err.retryAfterMs ?? BACKEND_RETRY_AFTER_FALLBACK_MS;
}

export function backendFailureSummary(err: unknown): string {
  if (!(err instanceof BackendRequestError)) return "unknown";
  return err.status === undefined ? err.kind : `${err.kind} ${err.status}`;
}

export function backendFailureStep(err: unknown): string {
  if (isNotLinked(err))
    return "This install is not linked - checking locally...";
  if (err instanceof BackendRequestError && err.status === 429) {
    return "The server is busy - checking locally...";
  }
  return "Server unreachable - checking locally...";
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
      throw new BackendRequestError(
        result.kind,
        result.status,
        result.detail,
        result.retryAfterMs,
      );
    }
    return result.body as T;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    if (onAbort) signal?.removeEventListener("abort", onAbort);
  }
}

export function sendBackendRequestAndForget(request: BackendRequest): void {
  void deliver(request).then((result) => {
    if (result.ok) return;
    console.warn(
      `[Blade] ${request.kind} report rejected:`,
      result.status === undefined
        ? result.kind
        : `${result.kind} ${result.status}`,
    );
  });
}
