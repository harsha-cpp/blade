import { afterEach, describe, expect, it, vi } from "vitest";
import {
  gmailTarget,
  injectIntoOpenTabs,
  isGmailUrl,
  isInjectableUrl,
  targetForUrl,
  webGuardTarget,
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
  it("reads the hashed loader paths out of the manifest at runtime", () => {
    expect(gmailTarget(BUILT_SCRIPTS)).toEqual({
      files: ["assets/index.ts-loader-x1YWvPn0.js"],
      allFrames: false,
    });
    expect(webGuardTarget(BUILT_SCRIPTS)).toEqual({
      files: ["assets/input-protection-entry.ts-loader-B2OXfe9w.js"],
      allFrames: true,
    });
  });

  it("has nothing to inject when the manifest lists no files", () => {
    expect(
      gmailTarget([{ matches: ["https://mail.google.com/*"] }]),
    ).toBeNull();
    expect(webGuardTarget([])).toBeNull();
  });

  it("sorts a url to the script that claims it", () => {
    const targets = {
      gmail: gmailTarget(BUILT_SCRIPTS),
      webGuard: webGuardTarget(BUILT_SCRIPTS),
    };

    expect(targetForUrl("https://mail.google.com/mail/u/0/", targets)).toBe(
      targets.gmail,
    );
    expect(targetForUrl("https://chat.example.com/", targets)).toBe(
      targets.webGuard,
    );
    expect(targetForUrl("chrome://extensions", targets)).toBeNull();
    expect(targetForUrl("", targets)).toBeNull();
  });

  it("recognises only real Gmail and only web pages", () => {
    expect(isGmailUrl("https://mail.google.com/mail/u/0/")).toBe(true);
    expect(isGmailUrl("https://mail.google.com.evil.test/")).toBe(false);
    expect(isInjectableUrl("https://example.test/")).toBe(true);
    expect(isInjectableUrl("chrome-extension://abc/popup.html")).toBe(false);
  });
});

describe("injecting into tabs that were already open", () => {
  it("injects the Gmail script and the web guard into the right tabs", async () => {
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

    const injected = await injectIntoOpenTabs();

    expect(injected).toBe(2);
    expect(executeScript).toHaveBeenCalledWith({
      target: { tabId: 1, allFrames: false },
      files: ["assets/index.ts-loader-x1YWvPn0.js"],
    });
    expect(executeScript).toHaveBeenCalledWith({
      target: { tabId: 2, allFrames: true },
      files: ["assets/input-protection-entry.ts-loader-B2OXfe9w.js"],
    });
  });

  it("keeps going when a tab refuses the injection", async () => {
    const executeScript = vi
      .fn()
      .mockRejectedValueOnce(new Error("Cannot access contents of the page"))
      .mockResolvedValue([]);
    fakeChrome(
      [
        { id: 1, url: "https://locked.example.test/" },
        { id: 2, url: "https://mail.google.com/mail/u/0/" },
      ],
      executeScript,
    );

    await expect(injectIntoOpenTabs()).resolves.toBe(1);
  });
});
