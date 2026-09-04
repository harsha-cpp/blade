import { afterEach, describe, expect, it, vi } from "vitest";
import { LAST_AUTH_OUTCOME_STORAGE_KEY } from "../auth-outcome";
import { BACKEND_URL } from "../config";
import { DEVICE_TOKEN_HEADER } from "../device-token";
import {
  BACKEND_REQUEST_MESSAGE,
  decodeBase64,
  encodeBase64,
  parseBackendRequest,
  type BackendRequest,
} from "../messages";
import type { WebBlockReport } from "../web-block";
import {
  executeBackendRequest,
  parseRetryAfter,
  prepareBackendRequest,
  prepareWebBlockRequest,
} from "./backend-bridge";
import type { ScanCredential } from "./credentials";

const ORG_CODE = "BLD-ABC123";
const DEVICE_TOKEN =
  "bladedev_0193f2c4-8e4a-7f11-9c33-4a2b1d6e5f70.qkR3s7Xm2wPzT8vLbN4hGdJyQ1eF6uCa";
const BY_ORG_CODE: ScanCredential = { kind: "org_code", orgCode: ORG_CODE };
const BY_DEVICE: ScanCredential = {
  kind: "device",
  token: DEVICE_TOKEN,
  orgCode: ORG_CODE,
};

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
    const { url, init } = prepareBackendRequest(eventRequest(), BY_ORG_CODE);
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
    const { init } = prepareBackendRequest(finalizeRequest(), BY_ORG_CODE);
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).not.toHaveProperty("user_email");
    expect(body.org_code).toBe(ORG_CODE);
    expect(body.attachment_scan_ids).toEqual(["att-1"]);
  });

  it("rebuilds the upload form from the transferred bytes", async () => {
    const bytes = new Uint8Array([37, 80, 68, 70, 1, 2, 3]);
    const { url, init } = prepareBackendRequest(
      uploadRequest(encodeBase64(bytes)),
      BY_ORG_CODE,
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
      BY_ORG_CODE,
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
      BY_ORG_CODE,
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
    const result = await executeBackendRequest(finalizeRequest(), BY_ORG_CODE);
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
    const result = await executeBackendRequest(finalizeRequest(), BY_ORG_CODE);
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
    const result = await executeBackendRequest(finalizeRequest(), BY_ORG_CODE);
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
    const result = await executeBackendRequest(request, BY_ORG_CODE);
    expect(result).toEqual({ ok: false, kind: "timeout" });
  });
});

describe("what leaves the browser", () => {
  it("drops a raw value fingerprint from a reported entity", () => {
    const parsed = parseBackendRequest({
      ...eventRequest(),
      entities: [
        { type: "IN_AADHAAR", confidence: 0.95, value_hash: "9f2b1c04" },
      ],
    });

    expect(parsed?.kind).toBe("event");
    const entities = parsed?.kind === "event" ? parsed.entities : [];
    expect(entities).toEqual([{ type: "IN_AADHAAR", confidence: 0.95 }]);
    expect(JSON.stringify(entities)).not.toContain("value_hash");
  });

  it("never puts a fingerprint in the request body", () => {
    const prepared = prepareBackendRequest(
      parseBackendRequest({
        ...eventRequest(),
        entities: [
          { type: "IN_AADHAAR", confidence: 0.95, value_hash: "9f2b1c04" },
        ],
      })!,
      BY_ORG_CODE,
    );

    expect(String(prepared.init.body)).not.toContain("value_hash");
    expect(String(prepared.init.body)).not.toContain("9f2b1c04");
  });
});

describe("Retry-After on a throttled response", () => {
  it("reads a delay given in seconds", () => {
    expect(parseRetryAfter("30")).toBe(30_000);
  });

  it("reads a delay given as a date", () => {
    const now = Date.now();
    const delay = parseRetryAfter(new Date(now + 5_000).toUTCString(), now);

    expect(delay).toBeGreaterThan(3_000);
    expect(delay).toBeLessThanOrEqual(5_000);
  });

  it("never asks the caller to wait a negative time", () => {
    expect(parseRetryAfter("-5")).toBe(0);
    expect(parseRetryAfter(new Date(Date.now() - 60_000).toUTCString())).toBe(
      0,
    );
  });

  it("ignores a missing or unreadable header", () => {
    expect(parseRetryAfter(null)).toBeUndefined();
    expect(parseRetryAfter("  ")).toBeUndefined();
    expect(parseRetryAfter("soon")).toBeUndefined();
  });

  it("passes the delay back with the failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "too many requests" }), {
            status: 429,
            headers: { "Retry-After": "12" },
          }),
      ),
    );

    const result = await executeBackendRequest(finalizeRequest(), BY_ORG_CODE);

    expect(result).toEqual({
      ok: false,
      kind: "http",
      status: 429,
      detail: "too many requests",
      retryAfterMs: 12_000,
    });
  });

  it("leaves other failures without a delay", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 500 })),
    );

    const result = await executeBackendRequest(finalizeRequest(), BY_ORG_CODE);

    expect(result).toMatchObject({ ok: false, status: 500 });
    expect((result as { retryAfterMs?: number }).retryAfterMs).toBeUndefined();
  });
});

describe("device token credential", () => {
  function headerOf(init: RequestInit): string | undefined {
    return (init.headers as Record<string, string> | undefined)?.[
      DEVICE_TOKEN_HEADER
    ];
  }

  it("sends the token and drops the org code from an event body", () => {
    const { init } = prepareBackendRequest(eventRequest(), BY_DEVICE);
    expect(headerOf(init)).toBe(DEVICE_TOKEN);
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).not.toHaveProperty("org_code");
    expect(body.client_event_id).toBe("scan-1");
    expect(String(init.body)).not.toContain(ORG_CODE);
  });

  it("sends the token and drops the org code from a finalize body", () => {
    const { init } = prepareBackendRequest(finalizeRequest(), BY_DEVICE);
    expect(headerOf(init)).toBe(DEVICE_TOKEN);
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).not.toHaveProperty("org_code");
    expect(body.attachment_scan_ids).toEqual(["att-1"]);
  });

  it("sends the token and drops the org code from an upload form", () => {
    const { init } = prepareBackendRequest(uploadRequest("AAAA"), BY_DEVICE);
    expect(headerOf(init)).toBe(DEVICE_TOKEN);
    const form = init.body as FormData;
    expect(form.get("org_code")).toBeNull();
    expect(form.get("client_scan_id")).toBe("scan-1");
  });

  it("sends the token and drops the org code from an attachment status url", () => {
    const { url, init } = prepareBackendRequest(
      {
        type: BACKEND_REQUEST_MESSAGE,
        kind: "attachment-status",
        timeoutMs: 5_000,
        attachmentScanId: "att-1",
      },
      BY_DEVICE,
    );
    expect(url).toBe(`${BACKEND_URL}/api/v1/scan/attachment/att-1`);
    expect(url).not.toContain("org_code");
    expect(headerOf(init)).toBe(DEVICE_TOKEN);
  });

  it("polls quarantine status with the device token and no org code", () => {
    const { url, init } = prepareBackendRequest(
      {
        type: BACKEND_REQUEST_MESSAGE,
        kind: "quarantine-status",
        timeoutMs: 8_000,
        quarantineId: "q1",
      },
      BY_DEVICE,
    );
    expect(url).toBe(`${BACKEND_URL}/api/v1/quarantine/q1/status`);
    expect(url).not.toContain("org_code");
    expect(headerOf(init)).toBe(DEVICE_TOKEN);
  });

  it("leaves the org code path untouched when no token is stored", () => {
    const { url, init } = prepareBackendRequest(eventRequest(), BY_ORG_CODE);
    expect(headerOf(init)).toBeUndefined();
    expect(url).toBe(`${BACKEND_URL}/api/v1/events`);
    expect(
      (JSON.parse(String(init.body)) as Record<string, unknown>).org_code,
    ).toBe(ORG_CODE);
  });
});

describe("web block report body", () => {
  const report: WebBlockReport = {
    type: "WEB_BLOCK",
    site_host: "chat.example.com",
    entities: [{ type: "MRN", confidence: 0.9, masked_value: "MRN-xxxx" }],
    risk_score: 74,
    severity: "high",
    reason: "sensitive-data",
  };

  it("keeps the org code and the last sender when there is no token", () => {
    const { init } = prepareWebBlockRequest(
      report,
      BY_ORG_CODE,
      "evt-1",
      "nurse@hospital.example",
    );
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body.org_code).toBe(ORG_CODE);
    expect(body.user_email).toBe("nurse@hospital.example");
    expect(body.channel).toBe("web");
  });

  it("drops the org code and the last sender when a token is stored", () => {
    const { init } = prepareWebBlockRequest(
      report,
      BY_DEVICE,
      "evt-1",
      "nurse@hospital.example",
    );
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).not.toHaveProperty("org_code");
    expect(body).not.toHaveProperty("user_email");
    expect(String(init.body)).not.toContain("nurse@hospital.example");
    expect((init.headers as Record<string, string>)[DEVICE_TOKEN_HEADER]).toBe(
      DEVICE_TOKEN,
    );
  });
});

describe("last auth outcome", () => {
  function stubSession(): Record<string, unknown> {
    const store: Record<string, unknown> = {};
    vi.stubGlobal("chrome", {
      storage: {
        session: {
          set: vi.fn(async (items: Record<string, unknown>) => {
            Object.assign(store, items);
          }),
        },
      },
    });
    return store;
  }

  function respondWith(status: number): void {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status })),
    );
  }

  it("records a verified outcome on a 200", async () => {
    const store = stubSession();
    respondWith(200);
    await executeBackendRequest(finalizeRequest(), BY_DEVICE);
    expect(store[LAST_AUTH_OUTCOME_STORAGE_KEY]).toMatchObject({ kind: "ok" });
  });

  it("records a rejection on a 401", async () => {
    const store = stubSession();
    respondWith(401);
    await executeBackendRequest(finalizeRequest(), BY_DEVICE);
    expect(store[LAST_AUTH_OUTCOME_STORAGE_KEY]).toMatchObject({
      kind: "unauthorized",
    });
  });

  it("records a rejection on a 403", async () => {
    const store = stubSession();
    respondWith(403);
    await executeBackendRequest(finalizeRequest(), BY_DEVICE);
    expect(store[LAST_AUTH_OUTCOME_STORAGE_KEY]).toMatchObject({
      kind: "unauthorized",
    });
  });

  it("records an unreachable backend as a network outcome", async () => {
    const store = stubSession();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("Failed to fetch");
      }),
    );
    await executeBackendRequest(finalizeRequest(), BY_DEVICE);
    expect(store[LAST_AUTH_OUTCOME_STORAGE_KEY]).toMatchObject({
      kind: "network",
    });
  });

  it("leaves the outcome alone on a server error", async () => {
    const store = stubSession();
    respondWith(500);
    await executeBackendRequest(finalizeRequest(), BY_DEVICE);
    expect(store[LAST_AUTH_OUTCOME_STORAGE_KEY]).toBeUndefined();
  });

  it("records nothing for an org code install", async () => {
    const store = stubSession();
    respondWith(401);
    await executeBackendRequest(finalizeRequest(), BY_ORG_CODE);
    expect(store[LAST_AUTH_OUTCOME_STORAGE_KEY]).toBeUndefined();
  });
});
