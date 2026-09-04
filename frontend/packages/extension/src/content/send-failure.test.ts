import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type { Verdict } from "@bladedlp/shared";

const NOTICE_SELECTOR = '[data-blade-surface="compose-notice"]';

const ALLOW_VERDICT: Verdict = {
  scan_id: "scan-1",
  action: "allow",
  severity: "low",
  risk_score: 0,
  matched_policy_ids: [],
  entities: [],
  recipients: [],
  user_message: "Nothing sensitive found.",
  created_at: "2026-01-01T00:00:00Z",
};

const outcome: { mode: "throw" | "allow" } = { mode: "throw" };

vi.mock("./compose", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./compose")>();
  return {
    ...actual,
    scanUntilStable: async (read: () => unknown) => {
      if (outcome.mode === "throw") throw new Error("the scan blew up");
      document.getElementById("send-group")?.remove();
      return { data: read(), verdict: ALLOW_VERDICT };
    },
  };
});

function buildCompose(): HTMLElement {
  const compose = document.createElement("div");
  compose.id = "compose";
  compose.setAttribute("role", "dialog");
  compose.innerHTML = [
    '<input name="to" value="rao@hospital.in" />',
    '<input name="subjectbox" value="Discharge" />',
    '<div role="textbox" g_editable="true" aria-label="Message Body">notes</div>',
    '<div id="send-group">',
    '<div id="send" role="button" aria-label="Send" data-tooltip="Send"></div>',
    '<div role="button" aria-label="More send options" aria-haspopup="true"></div>',
    "</div>",
  ].join("");
  return compose;
}

function clickSend(): void {
  document
    .getElementById("send")
    ?.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    );
}

function noticeText(): string {
  return document.querySelector(NOTICE_SELECTOR)?.textContent ?? "";
}

async function settle(): Promise<void> {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

describe("telling the user when a send is held back", () => {
  beforeAll(async () => {
    vi.stubGlobal("chrome", {
      runtime: { id: "blade-test" },
      storage: {
        local: { get: () => Promise.resolve({}), set: () => Promise.resolve() },
        onChanged: { addListener: () => undefined },
      },
    });
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    document.body.append(buildCompose());
    await import("./index");
  });

  afterAll(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("says nothing was sent when the scan throws", async () => {
    outcome.mode = "throw";
    document.querySelectorAll(NOTICE_SELECTOR).forEach((el) => el.remove());

    clickSend();
    await settle();

    expect(noticeText()).toContain(
      "Blade could not finish checking this message, so nothing was sent. Try again.",
    );
  });

  it("says the send button moved when the cleared control is gone", async () => {
    outcome.mode = "allow";
    document.querySelectorAll(NOTICE_SELECTOR).forEach((el) => el.remove());

    clickSend();
    await settle();

    expect(noticeText()).toContain("The send button moved. Click send again.");
  });
});
