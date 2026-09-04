import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CONTEXT_INVALIDATED_TEXT,
  isContextInvalidatedError,
  isExtensionContextValid,
  showContextInvalidatedBanner,
} from "./extension-context";

beforeEach(() => {
  document.body.replaceChildren();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("noticing that the extension was reloaded", () => {
  it("recognises the message Chrome throws at an orphaned script", () => {
    expect(
      isContextInvalidatedError(new Error("Extension context invalidated.")),
    ).toBe(true);
    expect(isContextInvalidatedError("Extension context invalidated")).toBe(
      true,
    );
  });

  it("does not mistake an ordinary failure for a reload", () => {
    expect(isContextInvalidatedError(new Error("Failed to fetch"))).toBe(false);
    expect(isContextInvalidatedError(undefined)).toBe(false);
  });

  it("treats a missing runtime id as a dead context", () => {
    vi.stubGlobal("chrome", { runtime: { id: "abcdef" } });
    expect(isExtensionContextValid()).toBe(true);

    vi.stubGlobal("chrome", { runtime: {} });
    expect(isExtensionContextValid()).toBe(false);

    vi.stubGlobal("chrome", undefined);
    expect(isExtensionContextValid()).toBe(false);
  });
});

describe("the reload banner", () => {
  it("asks the user to reload Gmail, once", () => {
    showContextInvalidatedBanner(document);
    showContextInvalidatedBanner(document);

    const banners = document.querySelectorAll('[role="alert"]');
    expect(banners.length).toBe(1);
    expect(banners[0]?.textContent).toBe(CONTEXT_INVALIDATED_TEXT);
    expect(CONTEXT_INVALIDATED_TEXT.split("\n").length).toBe(1);
  });
});
