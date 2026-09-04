import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ACCESS_REQUEST_PAGE_LIMIT,
  accessRequestsApi,
  sizeLabel,
  type AccessRequestPage,
} from "../api/access-requests";
import { setAccessToken } from "./api";
import {
  clearPlatformAccessProbes,
  probePlatformAccess,
} from "./platform-access";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function page(next: string | null): AccessRequestPage {
  return { items: [], next_cursor: next };
}

function requestedCall(
  mock: ReturnType<typeof vi.fn>,
  call = 0,
): [URL, RequestInit] {
  const args = mock.mock.calls[call];
  if (!args) throw new Error("fetch was not called");
  const [url, init] = args as unknown as [string, RequestInit];
  return [new URL(url), init];
}

beforeEach(() => {
  setAccessToken(null);
  clearPlatformAccessProbes();
});

afterEach(() => {
  vi.restoreAllMocks();
  setAccessToken(null);
  clearPlatformAccessProbes();
});

describe("accessRequestsApi", () => {
  it("leaves the status out when no filter is set", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, page("cursor-2")));
    vi.stubGlobal("fetch", fetchMock);

    const result = await accessRequestsApi.list();

    const [url] = requestedCall(fetchMock);
    expect(url.pathname).toBe("/api/v1/access-requests");
    expect(url.searchParams.has("status")).toBe(false);
    expect(url.searchParams.get("limit")).toBe(
      String(ACCESS_REQUEST_PAGE_LIMIT),
    );
    expect(result.next_cursor).toBe("cursor-2");
  });

  it("passes the status filter and the opaque cursor straight through", async () => {
    const cursor = "MjAyNi0wOS0wNFQwOTowMDowMCswMDowMHwwMTkyLXV1aWQ=";
    const fetchMock = vi.fn(async () => jsonResponse(200, page(null)));
    vi.stubGlobal("fetch", fetchMock);

    await accessRequestsApi.list({ status: "contacted", cursor });

    const [url] = requestedCall(fetchMock);
    expect(url.searchParams.get("status")).toBe("contacted");
    expect(url.searchParams.get("cursor")).toBe(cursor);
  });

  it("patches only the fields triage changed", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { id: "req-1", status: "approved" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await accessRequestsApi.update("req-1", {
      status: "approved",
      notes: "Called back, sending a trial invite.",
    });

    const [url, init] = requestedCall(fetchMock);
    expect(url.pathname).toBe("/api/v1/access-requests/req-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      status: "approved",
      notes: "Called back, sending a trial invite.",
    });
  });
});

describe("sizeLabel", () => {
  it("spells a bed range out in words", () => {
    expect(sizeLabel("500-1000")).toBe("500 to 1000 beds");
    expect(sizeLabel("under-100")).toBe("Under 100 beds");
    expect(sizeLabel("over-1000")).toBe("Over 1000 beds");
  });

  it("says so when the visitor skipped the question", () => {
    expect(sizeLabel(null)).toBe("Not given");
  });
});

describe("probePlatformAccess", () => {
  it("never asks the server when the member is not an owner", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, page(null)));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      probePlatformAccess({ role: "admin", email: "cto@hospital.example" }),
    ).resolves.toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("treats a 200 on the one-row probe as platform access", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, page(null)));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      probePlatformAccess({ role: "owner", email: "ops@blade.example" }),
    ).resolves.toBe(true);

    const [url] = requestedCall(fetchMock);
    expect(url.searchParams.get("limit")).toBe("1");
  });

  it("hides the page when the server says the owner is no operator", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(403, { detail: "not a platform operator" }),
      ),
    );

    await expect(
      probePlatformAccess({ role: "owner", email: "owner@hospital.example" }),
    ).resolves.toBe(false);
  });

  it("probes once per session and reuses the answer", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, page(null)));
    vi.stubGlobal("fetch", fetchMock);

    const identity = { role: "owner" as const, email: "ops@blade.example" };
    await probePlatformAccess(identity);
    await probePlatformAccess(identity);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retries after a server error rather than caching a wrong no", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(503, { detail: "down" }));
    vi.stubGlobal("fetch", fetchMock);

    const identity = { role: "owner" as const, email: "ops@blade.example" };
    await expect(probePlatformAccess(identity)).resolves.toBe(false);
    await expect(probePlatformAccess(identity)).resolves.toBe(false);

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
