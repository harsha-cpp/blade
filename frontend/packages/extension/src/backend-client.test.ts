import { afterEach, describe, expect, it, vi } from "vitest";
import {
  backendFailureStep,
  backendFailureSummary,
  BackendRequestError,
  isAttachmentRejected,
  retryDelayMs,
  sendBackendRequest,
  sendBackendRequestAndForget,
} from "./backend-client";
import {
  BACKEND_MESSAGE_GRACE_MS,
  BACKEND_RETRY_AFTER_FALLBACK_MS,
} from "./config";
import { BACKEND_REQUEST_MESSAGE, type BackendRequest } from "./messages";

function statusRequest(timeoutMs = 5_000): BackendRequest {
  return {
    type: BACKEND_REQUEST_MESSAGE,
    kind: "quarantine-status",
    timeoutMs,
    quarantineId: "q1",
  };
}

function stubSendMessage(impl: (message: unknown) => Promise<unknown>): void {
  vi.stubGlobal("chrome", { runtime: { sendMessage: vi.fn(impl) } });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("backend requests from the page context", () => {
  it("returns the body the worker fetched", async () => {
    stubSendMessage(async () => ({
      ok: true,
      status: 200,
      body: { status: "approved" },
    }));
    await expect(sendBackendRequest(statusRequest())).resolves.toEqual({
      status: "approved",
    });
  });

  it("sends the request to the worker unchanged", async () => {
    const sendMessage = vi.fn(async () => ({ ok: true, status: 200, body: 1 }));
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const request = statusRequest();
    await sendBackendRequest(request);
    expect(sendMessage).toHaveBeenCalledWith(request);
  });

  it("throws a typed error for an http failure", async () => {
    stubSendMessage(async () => ({
      ok: false,
      kind: "http",
      status: 422,
      detail: "bad request",
    }));
    const error = await sendBackendRequest(statusRequest()).catch(
      (err: unknown) => err,
    );
    expect(error).toBeInstanceOf(BackendRequestError);
    expect(error).toMatchObject({
      kind: "http",
      status: 422,
      message: "bad request",
    });
  });

  it("treats a dead service worker as a network failure", async () => {
    stubSendMessage(async () => {
      throw new Error("Receiving end does not exist");
    });
    const error = await sendBackendRequest(statusRequest()).catch(
      (err: unknown) => err,
    );
    expect(error).toMatchObject({ kind: "network" });
  });

  it("gives up when the worker never answers", async () => {
    vi.useFakeTimers();
    stubSendMessage(() => new Promise(() => undefined));
    const pending = sendBackendRequest(statusRequest(1_000)).catch(
      (err: unknown) => err,
    );
    await vi.advanceTimersByTimeAsync(1_000 + BACKEND_MESSAGE_GRACE_MS + 1);
    expect(await pending).toMatchObject({ kind: "timeout" });
  });

  it("waits out a cold service worker before calling it a timeout", async () => {
    vi.useFakeTimers();
    stubSendMessage(() => new Promise(() => undefined));
    let settled = false;
    const pending = sendBackendRequest(statusRequest(1_000))
      .catch((err: unknown) => err)
      .finally(() => {
        settled = true;
      });

    await vi.advanceTimersByTimeAsync(1_000 + 2_000);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(BACKEND_MESSAGE_GRACE_MS);
    expect(await pending).toMatchObject({ kind: "timeout" });
  });

  it("stops waiting once the scan budget aborts", async () => {
    stubSendMessage(() => new Promise(() => undefined));
    const controller = new AbortController();
    const pending = sendBackendRequest(
      statusRequest(),
      controller.signal,
    ).catch((err: unknown) => err);
    controller.abort();
    expect(await pending).toMatchObject({ kind: "timeout" });
  });

  it("refuses immediately when the signal is already aborted", async () => {
    const sendMessage = vi.fn(async () => ({ ok: true, status: 200, body: 1 }));
    vi.stubGlobal("chrome", { runtime: { sendMessage } });
    const controller = new AbortController();
    controller.abort();
    await expect(
      sendBackendRequest(statusRequest(), controller.signal),
    ).rejects.toBeInstanceOf(BackendRequestError);
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("swallows failures of a fire and forget report", async () => {
    stubSendMessage(async () => {
      throw new Error("Receiving end does not exist");
    });
    expect(() => sendBackendRequestAndForget(statusRequest())).not.toThrow();
  });

  it("warns when a fire and forget report is rejected", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    stubSendMessage(async () => ({
      ok: false,
      kind: "http",
      status: 422,
      detail: "recipient rao@hospital.in is not an address",
    }));

    sendBackendRequestAndForget(statusRequest());
    await Promise.resolve();
    await Promise.resolve();

    expect(warn).toHaveBeenCalledWith(
      "[Blade] quarantine-status report rejected:",
      "http 422",
    );
    expect(JSON.stringify(warn.mock.calls)).not.toContain("rao@hospital.in");
  });

  it("says nothing when a fire and forget report is accepted", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    warn.mockClear();
    stubSendMessage(async () => ({ ok: true, status: 202, body: null }));

    sendBackendRequestAndForget(statusRequest());
    await Promise.resolve();
    await Promise.resolve();

    expect(warn).not.toHaveBeenCalled();
  });
});

describe("summarising a backend failure for the page console", () => {
  it("names the kind and the status and nothing else", () => {
    expect(
      backendFailureSummary(
        new BackendRequestError("http", 422, "recipient rao@hospital.in"),
      ),
    ).toBe("http 422");
    expect(backendFailureSummary(new BackendRequestError("network"))).toBe(
      "network",
    );
    expect(backendFailureSummary(new Error("boom"))).toBe("unknown");
  });
});

describe("what the user is told when a scan cannot reach the backend", () => {
  it.each([
    [401, "This install is not linked - checking locally..."],
    [403, "This install is not linked - checking locally..."],
    [429, "The server is busy - checking locally..."],
    [500, "Server unreachable - checking locally..."],
  ])("explains a %s in its own words", (status, message) => {
    expect(backendFailureStep(new BackendRequestError("http", status))).toBe(
      message,
    );
  });

  it("calls anything else unreachable", () => {
    expect(backendFailureStep(new BackendRequestError("network"))).toBe(
      "Server unreachable - checking locally...",
    );
    expect(backendFailureStep(new Error("boom"))).toBe(
      "Server unreachable - checking locally...",
    );
  });
});

describe("back pressure and attachments the server will not take", () => {
  it("waits as long as a 429 asked for", () => {
    expect(
      retryDelayMs(new BackendRequestError("http", 429, "slow down", 30_000)),
    ).toBe(30_000);
  });

  it("waits a short while when a 429 named no delay", () => {
    expect(retryDelayMs(new BackendRequestError("http", 429))).toBe(
      BACKEND_RETRY_AFTER_FALLBACK_MS,
    );
  });

  it("does not retry anything that is not a 429", () => {
    expect(retryDelayMs(new BackendRequestError("http", 500))).toBeNull();
    expect(retryDelayMs(new BackendRequestError("timeout"))).toBeNull();
    expect(retryDelayMs(new Error("boom"))).toBeNull();
  });

  it("treats only a 413 as an attachment the server refused", () => {
    expect(isAttachmentRejected(new BackendRequestError("http", 413))).toBe(
      true,
    );
    expect(isAttachmentRejected(new BackendRequestError("http", 500))).toBe(
      false,
    );
    expect(isAttachmentRejected(new Error("boom"))).toBe(false);
  });

  it("carries the delay from the worker into the error", async () => {
    stubSendMessage(async () => ({
      ok: false,
      kind: "http",
      status: 429,
      detail: "too many requests",
      retryAfterMs: 12_000,
    }));

    await expect(sendBackendRequest(statusRequest())).rejects.toMatchObject({
      status: 429,
      retryAfterMs: 12_000,
    });
  });
});
