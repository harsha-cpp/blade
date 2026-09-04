import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Verdict } from "@bladedlp/shared";
import { mountWarningModal } from "./mount";

async function settle(): Promise<void> {
  await vi.advanceTimersByTimeAsync(20);
}

const blocked: Verdict = {
  scan_id: "scan-1",
  action: "block",
  severity: "high",
  risk_score: 90,
  matched_policy_ids: ["local-phi-policy"],
  entities: [],
  recipients: [],
  user_message: "Blocked.",
  created_at: new Date().toISOString(),
};

beforeEach(() => {
  vi.useFakeTimers();
  document.body.replaceChildren();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("mounting the warning modal", () => {
  it("puts a host on the page and takes it away again", async () => {
    const cleanup = mountWarningModal(
      { getElement: () => document.body, send: () => {} },
      blocked,
    );
    await settle();
    expect(document.body.children.length).toBe(1);

    cleanup();

    expect(document.body.children.length).toBe(0);
  });

  it("removes the overlay when the modal throws while rendering", async () => {
    const malformed = { ...blocked, entities: undefined } as unknown as Verdict;

    mountWarningModal(
      { getElement: () => document.body, send: () => {} },
      malformed,
    );
    await settle();

    expect(document.body.children.length).toBe(0);
  });

  it("never sends when the modal fails to render", async () => {
    const send = vi.fn();
    const malformed = { ...blocked, entities: undefined } as unknown as Verdict;

    mountWarningModal({ getElement: () => document.body, send }, malformed);
    await settle();

    expect(send).not.toHaveBeenCalled();
  });
});
