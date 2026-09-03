import { BACKEND_URL } from "../config";
import {
  decodeBase64,
  type BackendRequest,
  type BackendResult,
} from "../messages";

export interface PreparedBackendRequest {
  url: string;
  init: RequestInit;
}

function jsonInit(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function uploadForm(
  request: Extract<BackendRequest, { kind: "attachment-upload" }>,
  orgCode: string,
): FormData {
  const form = new FormData();
  form.append("org_code", orgCode);
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
  orgCode: string,
): PreparedBackendRequest {
  switch (request.kind) {
    case "event":
      return {
        url: `${BACKEND_URL}/api/v1/events`,
        init: jsonInit({
          org_code: orgCode,
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
        init: { method: "POST", body: uploadForm(request, orgCode) },
      };
    case "attachment-status":
      return {
        url: `${BACKEND_URL}/api/v1/scan/attachment/${encodeURIComponent(
          request.attachmentScanId,
        )}?org_code=${encodeURIComponent(orgCode)}`,
        init: { method: "GET" },
      };
    case "scan-finalize":
      return {
        url: `${BACKEND_URL}/api/v1/scan/finalize`,
        init: jsonInit({
          org_code: orgCode,
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
        )}/status?org_code=${encodeURIComponent(orgCode)}`,
        init: { method: "GET" },
      };
  }
}

function failureDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    return String((body as { detail: unknown }).detail);
  }
  return fallback;
}

export async function executeBackendRequest(
  request: BackendRequest,
  orgCode: string,
): Promise<BackendResult> {
  const { url, init } = prepareBackendRequest(request, orgCode);
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

export async function storedOrgCode(): Promise<string | null> {
  const stored = await chrome.storage.local.get("blade_org_code");
  const code = (stored.blade_org_code as string | undefined)
    ?.trim()
    .toUpperCase();
  return code ? code : null;
}

export async function handleBackendRequest(
  request: BackendRequest,
): Promise<BackendResult> {
  const orgCode = await storedOrgCode();
  if (!orgCode) {
    return {
      ok: false,
      kind: "network",
      detail: "this install has no organization code",
    };
  }
  return executeBackendRequest(request, orgCode);
}
