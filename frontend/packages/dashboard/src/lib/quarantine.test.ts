import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  QUARANTINE_PAGE_LIMIT,
  quarantineApi,
  type QuarantinePage,
} from "../api/quarantine";
import { ApiError, setAccessToken } from "./api";
import { ALREADY_DECIDED_MESSAGE, decisionErrorMessage } from "./quarantine";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function page(next: string | null): QuarantinePage {
  return { items: [], next_cursor: next };
}

function requestedUrl(mock: ReturnType<typeof vi.fn>, call = 0): URL {
  const args = mock.mock.calls[call];
  if (!args) throw new Error("fetch was not called");
  return new URL(args[0] as string);
}

describe("quarantineApi.list", () => {
  beforeEach(() => {
    setAccessToken(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setAccessToken(null);
  });

  it("asks for the pending page by default and reads the paged envelope", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, page("Y3JlYXRlZC1hdHxpZA==")),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await quarantineApi.list();

    const url = requestedUrl(fetchMock);
    expect(url.pathname).toBe("/api/v1/quarantine");
    expect(url.searchParams.get("status")).toBe("pending");
    expect(url.searchParams.get("limit")).toBe(String(QUARANTINE_PAGE_LIMIT));
    expect(url.searchParams.has("cursor")).toBe(false);
    expect(result.next_cursor).toBe("Y3JlYXRlZC1hdHxpZA==");
  });

  it("sends the cursor back untouched instead of decoding it", async () => {
    const cursor = "MjAyNi0wOS0wNFQwOTowMDowMCswMDowMHwwMTkyLXV1aWQ=";
    const fetchMock = vi.fn(async () => jsonResponse(200, page(null)));
    vi.stubGlobal("fetch", fetchMock);

    await quarantineApi.list({ status: "all", cursor });

    const url = requestedUrl(fetchMock);
    expect(url.searchParams.get("status")).toBe("all");
    expect(url.searchParams.get("cursor")).toBe(cursor);
  });

  it("keeps the last page from asking for another one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(200, page(null))),
    );

    const result = await quarantineApi.list({ status: "approved" });

    expect(result.next_cursor).toBeNull();
    expect(result.items).toEqual([]);
  });

  it("surfaces a rejected cursor as a 400 rather than an empty page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(400, { detail: "invalid cursor" })),
    );

    await expect(
      quarantineApi.list({ cursor: "not-a-cursor" }),
    ).rejects.toMatchObject({
      status: 400,
      detail: "invalid cursor",
    });
  });
});

describe("decisionErrorMessage", () => {
  it("explains a 409 as another reviewer having got there first", () => {
    expect(
      decisionErrorMessage(new ApiError(409, "quarantine already decided")),
    ).toBe(ALREADY_DECIDED_MESSAGE);
  });

  it("keeps the server's own wording for every other failure", () => {
    expect(decisionErrorMessage(new ApiError(403, "insufficient role"))).toBe(
      "insufficient role",
    );
  });

  it("falls back to a decision-specific message when there is no detail", () => {
    expect(decisionErrorMessage(new Error(""))).toBe(
      "Could not record that decision.",
    );
  });
});
