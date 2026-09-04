import { afterEach, describe, expect, it, vi } from "vitest";
import {
  gmailTarget,
  injectIntoOpenTabs,
  injectionSummaryLine,
  isGmailUrl,
  type ManifestContentScript,
} from "./inject";

const BUILT_SCRIPTS: ManifestContentScript[] = [
  {
    js: ["assets/index.ts-loader-x1YWvPn0.js"],
    matches: ["https://mail.google.com/*"],
    all_frames: false,
  },
  {
    js: ["assets/input-protection-entry.ts-loader-B2OXfe9w.js"],
    matches: ["http://*/*", "https://*/*"],
    all_frames: true,
  },
];

interface FakeTab {
  id?: number | undefined;
  url?: string | undefined;
  pendingUrl?: string | undefined;
}

function fakeChrome(tabs: FakeTab[], executeScript = vi.fn()) {
  const api = {
    runtime: { getManifest: () => ({ content_scripts: BUILT_SCRIPTS }) },
    tabs: { query: vi.fn().mockResolvedValue(tabs) },
    scripting: { executeScript },
  };
  vi.stubGlobal("chrome", api);
  return api;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("picking the built content script files", () => {
  it("reads the hashed Gmail loader path out of the manifest at runtime", () => {
    expect(gmailTarget(BUILT_SCRIPTS)).toEqual({
      files: ["assets/index.ts-loader-x1YWvPn0.js"],
      allFrames: false,
    });
  });

  it("has nothing to inject when the manifest lists no files", () => {
    expect(
      gmailTarget([{ matches: ["https://mail.google.com/*"] }]),
    ).toBeNull();
    expect(gmailTarget([])).toBeNull();
  });

  it("recognises only real Gmail", () => {
    expect(isGmailUrl("https://mail.google.com/mail/u/0/")).toBe(true);
    expect(isGmailUrl("https://mail.google.com.evil.test/")).toBe(false);
    expect(isGmailUrl("chrome://extensions")).toBe(false);
    expect(isGmailUrl("")).toBe(false);
  });
});

describe("injecting into tabs that were already open", () => {
  it("injects only into Gmail tabs and counts the rest as skipped", async () => {
    const executeScript = vi.fn().mockResolvedValue([]);
    fakeChrome(
      [
        { id: 1, url: "https://mail.google.com/mail/u/0/" },
        { id: 2, url: "https://chat.example.com/" },
        { id: 3, url: "chrome://extensions" },
        { id: undefined, url: "https://example.test/" },
      ],
      executeScript,
    );

    const summary = await injectIntoOpenTabs();

    expect(summary).toEqual({ injected: 1, skipped: 2 });
    expect(executeScript).toHaveBeenCalledTimes(1);
    expect(executeScript).toHaveBeenCalledWith({
      target: { tabId: 1, allFrames: false },
      files: ["assets/index.ts-loader-x1YWvPn0.js"],
    });
  });

  it("never tries a non-Gmail tab it has no host permission for", async () => {
    const executeScript = vi.fn().mockResolvedValue([]);
    fakeChrome([{ id: 2, url: "https://chat.example.com/" }], executeScript);

    await expect(injectIntoOpenTabs()).resolves.toEqual({
      injected: 0,
      skipped: 1,
    });
    expect(executeScript).not.toHaveBeenCalled();
  });

  it("keeps going when a Gmail tab refuses the injection", async () => {
    const executeScript = vi
      .fn()
      .mockRejectedValueOnce(new Error("Cannot access contents of the page"))
      .mockResolvedValue([]);
    fakeChrome(
      [
        { id: 1, url: "https://mail.google.com/mail/u/0/" },
        { id: 2, url: "https://mail.google.com/mail/u/1/" },
      ],
      executeScript,
    );

    await expect(injectIntoOpenTabs()).resolves.toEqual({
      injected: 1,
      skipped: 1,
    });
  });

  it("summarises both counts in one line", () => {
    expect(injectionSummaryLine({ injected: 2, skipped: 7 })).toContain(
      "2 open tab(s)",
    );
    expect(injectionSummaryLine({ injected: 2, skipped: 7 })).toContain(
      "7 tab(s)",
    );
  });
});
