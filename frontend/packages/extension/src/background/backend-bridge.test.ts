import { afterEach, describe, expect, it, vi } from "vitest";
import { BACKEND_URL } from "../config";
import {
  BACKEND_REQUEST_MESSAGE,
  decodeBase64,
  encodeBase64,
  parseBackendRequest,
  type BackendRequest,
} from "../messages";
import { executeBackendRequest, prepareBackendRequest } from "./backend-bridge";

const ORG_CODE = "BLD-ABC123";

function eventRequest(): BackendRequest {
  return {
    type: BACKEND_REQUEST_MESSAGE,
    kind: "event",
    timeoutMs: 10_000,
    clientEventId: "scan-1",
    userEmail: "nurse@hospital.example",
    action: "block",
    severity: "high",
    riskScore: 82,
    entities: [{ type: "MRN", confidence: 0.9 }],
    recipients: ["outside@example.com"],
    timestamp: "2026-09-04T10:00:00Z",
  };
}

function finalizeRequest(): BackendRequest {
  return {
    type: BACKEND_REQUEST_MESSAGE,
    kind: "scan-finalize",
    timeoutMs: 10_000,
    clientScanId: "scan-1",
    subject: "Discharge",
    body: "Patient summary",
    recipients: ["outside@example.com"],
    userEmail: null,
    attachmentScanIds: ["att-1"],
  };
}

function uploadRequest(dataBase64: string): BackendRequest {
  return {
    type: BACKEND_REQUEST_MESSAGE,
    kind: "attachment-upload",
    timeoutMs: 15_000,
    clientScanId: "scan-1",
    attachmentId: "att-1",
    filename: "report.pdf",
    mimeType: "application/pdf",
    dataBase64,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("backend request validation", () => {
  it("accepts a well formed request", () => {
    expect(parseBackendRequest(eventRequest())).not.toBeNull();
  });

  it("rejects a message that is not a backend request", () => {
    expect(parseBackendRequest({ type: "WEB_BLOCK" })).toBeNull();
    expect(parseBackendRequest(null)).toBeNull();
    expect(parseBackendRequest("finalize")).toBeNull();
  });

  it("rejects a known kind with a missing field", () => {
    const broken = { ...finalizeRequest(), clientScanId: "" };
    expect(parseBackendRequest(broken)).toBeNull();
  });

  it("rejects an out of range timeout", () => {
    const broken = { ...eventRequest(), timeoutMs: 10 * 60_000 };
    expect(parseBackendRequest(broken)).toBeNull();
  });
});

describe("request building", () => {
  it("posts an event with the stored org code", () => {
    const { url, init } = prepareBackendRequest(eventRequest(), ORG_CODE);
    expect(url).toBe(`${BACKEND_URL}/api/v1/events`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      org_code: ORG_CODE,
      client_event_id: "scan-1",
      user_email: "nurse@hospital.example",
      action: "block",
      severity: "high",
      risk_score: 82,
      entities: [{ type: "MRN", confidence: 0.9 }],
      recipients: ["outside@example.com"],
      timestamp: "2026-09-04T10:00:00Z",
    });
  });

  it("omits the sender address when the page did not resolve one", () => {
    const { init } = prepareBackendRequest(finalizeRequest(), ORG_CODE);
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).not.toHaveProperty("user_email");
    expect(body.org_code).toBe(ORG_CODE);
    expect(body.attachment_scan_ids).toEqual(["att-1"]);
  });

  it("rebuilds the upload form from the transferred bytes", async () => {
    const bytes = new Uint8Array([37, 80, 68, 70, 1, 2, 3]);
    const { url, init } = prepareBackendRequest(
      uploadRequest(encodeBase64(bytes)),
      ORG_CODE,
    );
    expect(url).toBe(`${BACKEND_URL}/api/v1/scan/attachment`);
    const form = init.body as FormData;
    expect(form.get("org_code")).toBe(ORG_CODE);
    expect(form.get("client_scan_id")).toBe("scan-1");
    expect(form.get("attachment_id")).toBe("att-1");
    const file = form.get("file") as File;
    expect(file.name).toBe("report.pdf");
    expect(file.type).toBe("application/pdf");
    expect(new Uint8Array(await file.arrayBuffer())).toEqual(bytes);
  });

  it("round trips bytes through base64", () => {
    const bytes = new Uint8Array(70_000);
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = i % 256;
    expect(decodeBase64(encodeBase64(bytes))).toEqual(bytes);
  });

  it("encodes ids in the attachment status url", () => {
    const { url, init } = prepareBackendRequest(
      {
        type: BACKEND_REQUEST_MESSAGE,
        kind: "attachment-status",
        timeoutMs: 5_000,
        attachmentScanId: "a/b c",
      },
      ORG_CODE,
    );
    expect(url).toBe(
      `${BACKEND_URL}/api/v1/scan/attachment/a%2Fb%20c?org_code=BLD-ABC123`,
    );
    expect(init.method).toBe("GET");
  });

  it("encodes ids in the quarantine status url", () => {
    const { url } = prepareBackendRequest(
      {
        type: BACKEND_REQUEST_MESSAGE,
        kind: "quarantine-status",
        timeoutMs: 8_000,
        quarantineId: "q 1",
      },
      ORG_CODE,
    );
    expect(url).toBe(
      `${BACKEND_URL}/api/v1/quarantine/q%201/status?org_code=BLD-ABC123`,
    );
  });
});

describe("outcome mapping", () => {
  it("returns the parsed body on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ scan_id: "s1" }))),
    );
    const result = await executeBackendRequest(finalizeRequest(), ORG_CODE);
    expect(result).toEqual({ ok: true, status: 200, body: { scan_id: "s1" } });
  });

  it("maps a rejected response to an http failure with the detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "unknown organization" }), {
            status: 404,
          }),
      ),
    );
    const result = await executeBackendRequest(finalizeRequest(), ORG_CODE);
    expect(result).toEqual({
      ok: false,
      kind: "http",
      status: 404,
      detail: "unknown organization",
    });
  });

  it("maps a transport failure to a network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("Failed to fetch");
      }),
    );
    const result = await executeBackendRequest(finalizeRequest(), ORG_CODE);
    expect(result).toMatchObject({ ok: false, kind: "network" });
  });

  it("aborts and reports a timeout when the backend does not answer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () =>
              reject(new Error("aborted")),
            );
          }),
      ),
    );
    const request = { ...finalizeRequest(), timeoutMs: 5 };
    const result = await executeBackendRequest(request, ORG_CODE);
    expect(result).toEqual({ ok: false, kind: "timeout" });
  });
});
