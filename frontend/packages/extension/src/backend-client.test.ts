import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BackendRequestError,
  sendBackendRequest,
  sendBackendRequestAndForget,
} from "./backend-client";
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
    await vi.advanceTimersByTimeAsync(2_000);
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
});
