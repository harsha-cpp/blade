import {
  authOutcomeForResult,
  recordAuthOutcome,
  type AuthOutcome,
} from "../auth-outcome";
import { BACKEND_URL } from "../config";
import { DEVICE_TOKEN_HEADER } from "../device-token";
import {
  decodeBase64,
  type BackendRequest,
  type BackendResult,
} from "../messages";
import type { WebBlockReport } from "../web-block";
import { resolveScanCredential, type ScanCredential } from "./credentials";

export interface PreparedBackendRequest {
  url: string;
  init: RequestInit;
}

type BackendRequestKind = BackendRequest["kind"];

export function isDeviceAuthenticated(
  credential: ScanCredential,
  _kind: BackendRequestKind,
): boolean {
  return credential.kind === "device";
}

function orgCodeSentWith(
  credential: ScanCredential,
  kind: BackendRequestKind,
): string | null {
  if (credential.kind === "org_code") return credential.orgCode;
  return isDeviceAuthenticated(credential, kind) ? null : credential.orgCode;
}

function credentialHeaders(
  credential: ScanCredential,
  base: Record<string, string> = {},
): Record<string, string> {
  if (credential.kind !== "device") return base;
  return { ...base, [DEVICE_TOKEN_HEADER]: credential.token };
}

function jsonInit(credential: ScanCredential, body: unknown): RequestInit {
  return {
    method: "POST",
    headers: credentialHeaders(credential, {
      "Content-Type": "application/json",
    }),
    body: JSON.stringify(body),
  };
}

function uploadForm(
  request: Extract<BackendRequest, { kind: "attachment-upload" }>,
  orgCode: string | null,
): FormData {
  const form = new FormData();
  if (orgCode) form.append("org_code", orgCode);
  form.append("client_scan_id", request.clientScanId);
  form.append("attachment_id", request.attachmentId);
  const bytes = decodeBase64(request.dataBase64);
  form.append(
    "file",
    new File([bytes], request.filename, { type: request.mimeType }),
  );
  return form;
}

export function prepareBackendRequest(
  request: BackendRequest,
  credential: ScanCredential,
): PreparedBackendRequest {
  const orgCode = orgCodeSentWith(credential, request.kind);
  const orgCodeField = orgCode ? { org_code: orgCode } : {};
  const orgCodeQuery = orgCode
    ? `?org_code=${encodeURIComponent(orgCode)}`
    : "";

  switch (request.kind) {
    case "event":
      return {
        url: `${BACKEND_URL}/api/v1/events`,
        init: jsonInit(credential, {
          ...orgCodeField,
          client_event_id: request.clientEventId,
          ...(request.userEmail ? { user_email: request.userEmail } : {}),
          action: request.action,
          severity: request.severity,
          risk_score: request.riskScore,
          entities: request.entities,
          recipients: request.recipients,
          timestamp: request.timestamp,
        }),
      };
    case "attachment-upload":
      return {
        url: `${BACKEND_URL}/api/v1/scan/attachment`,
        init: {
          method: "POST",
          headers: credentialHeaders(credential),
          body: uploadForm(request, orgCode),
        },
      };
    case "attachment-status":
      return {
        url: `${BACKEND_URL}/api/v1/scan/attachment/${encodeURIComponent(
          request.attachmentScanId,
        )}${orgCodeQuery}`,
        init: { method: "GET", headers: credentialHeaders(credential) },
      };
    case "scan-finalize":
      return {
        url: `${BACKEND_URL}/api/v1/scan/finalize`,
        init: jsonInit(credential, {
          ...orgCodeField,
          client_scan_id: request.clientScanId,
          subject: request.subject,
          body: request.body,
          recipients: request.recipients,
          ...(request.userEmail ? { user_email: request.userEmail } : {}),
          attachment_scan_ids: request.attachmentScanIds,
        }),
      };
    case "quarantine-status":
      return {
        url: `${BACKEND_URL}/api/v1/quarantine/${encodeURIComponent(
          request.quarantineId,
        )}/status${orgCodeQuery}`,
        init: { method: "GET", headers: credentialHeaders(credential) },
      };
  }
}

export function prepareWebBlockRequest(
  report: WebBlockReport,
  credential: ScanCredential,
  clientEventId: string,
  userEmail: string | null,
): PreparedBackendRequest {
  const reporter =
    credential.kind === "device"
      ? {}
      : { org_code: credential.orgCode, user_email: userEmail };
  return {
    url: `${BACKEND_URL}/api/v1/events`,
    init: jsonInit(credential, {
      ...reporter,
      client_event_id: clientEventId,
      action: "block",
      severity: report.severity,
      risk_score: report.risk_score,
      entities: report.entities,
      recipients: [],
      channel: "web",
      site_host: report.site_host,
    }),
  };
}

export function parseRetryAfter(
  value: string | null,
  now: number = Date.now(),
): number | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const seconds = Number(trimmed);
  if (Number.isFinite(seconds)) return Math.max(0, Math.round(seconds * 1000));
  const at = Date.parse(trimmed);
  if (Number.isNaN(at)) return undefined;
  return Math.max(0, at - now);
}

function failureDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    return String((body as { detail: unknown }).detail);
  }
  return fallback;
}

async function sendBackendRequest(
  request: BackendRequest,
  credential: ScanCredential,
): Promise<BackendResult> {
  const { url, init } = prepareBackendRequest(request, credential);
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, request.timeoutMs);

  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    const body = (await res.json().catch(() => null)) as unknown;
    if (!res.ok) {
      return {
        ok: false,
        kind: "http",
        status: res.status,
        detail: failureDetail(body, res.statusText),
        retryAfterMs:
          res.status === 429
            ? parseRetryAfter(res.headers.get("Retry-After"))
            : undefined,
      };
    }
    return { ok: true, status: res.status, body };
  } catch (err) {
    if (timedOut) return { ok: false, kind: "timeout" };
    return { ok: false, kind: "network", detail: (err as Error).message };
  } finally {
    clearTimeout(timer);
  }
}

export async function executeBackendRequest(
  request: BackendRequest,
  credential: ScanCredential,
): Promise<BackendResult> {
  const result = await sendBackendRequest(request, credential);
  if (isDeviceAuthenticated(credential, request.kind)) {
    const outcome: AuthOutcome | null = authOutcomeForResult(
      result,
      Date.now(),
    );
    await recordAuthOutcome(outcome);
  }
  return result;
}

export async function handleBackendRequest(
  request: BackendRequest,
): Promise<BackendResult> {
  const credential = await resolveScanCredential();
  if (!credential) {
    return {
      ok: false,
      kind: "network",
      detail: "this install has no device token or organization code",
    };
  }
  return executeBackendRequest(request, credential);
}
