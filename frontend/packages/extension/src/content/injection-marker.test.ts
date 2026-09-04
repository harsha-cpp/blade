import { describe, expect, it } from "vitest";
import {
  claimInjection,
  GMAIL_GUARD_MARKER,
  WEB_GUARD_MARKER,
} from "./injection-marker";

describe("double injection guard", () => {
  it("lets the first script in and turns the second away", () => {
    const scope = {};

    expect(claimInjection(GMAIL_GUARD_MARKER, scope)).toBe(true);
    expect(claimInjection(GMAIL_GUARD_MARKER, scope)).toBe(false);
    expect(claimInjection(GMAIL_GUARD_MARKER, scope)).toBe(false);
  });

  it("keeps the Gmail and web guards independent", () => {
    const scope = {};

    expect(claimInjection(GMAIL_GUARD_MARKER, scope)).toBe(true);
    expect(claimInjection(WEB_GUARD_MARKER, scope)).toBe(true);
  });

  it("keeps the marker on the script scope, not on the page", () => {
    const scope: Record<string, unknown> = {};
    claimInjection(GMAIL_GUARD_MARKER, scope);

    expect(scope[GMAIL_GUARD_MARKER]).toBe(true);
    expect(document.documentElement.hasAttribute(GMAIL_GUARD_MARKER)).toBe(
      false,
    );
    expect(document.querySelector(`[${GMAIL_GUARD_MARKER}]`)).toBeNull();
  });

  it("runs the script rather than blocking it when the scope is frozen", () => {
    expect(claimInjection(GMAIL_GUARD_MARKER, Object.freeze({}))).toBe(true);
  });
});
