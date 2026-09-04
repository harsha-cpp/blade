import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  boundComposeStillOwns,
  composeKey,
  isBladeSurface,
  scopedSurfaceId,
} from "./compose-scope";
import { showScanProgress } from "./progress";

function compose(): HTMLElement {
  const el = document.createElement("div");
  document.body.append(el);
  return el;
}

beforeEach(() => {
  document.body.replaceChildren();
});

describe("scoping Blade surfaces to one compose", () => {
  it("gives every compose its own stable key", () => {
    const first = compose();
    const second = compose();

    expect(composeKey(first)).toBe(composeKey(first));
    expect(composeKey(first)).not.toBe(composeKey(second));
  });

  it("keeps two concurrent progress strips on screen", () => {
    const first = compose();
    const second = compose();

    const a = showScanProgress(first, () => undefined);
    const b = showScanProgress(second, () => undefined);
    a.setStep("Checking the first draft...");
    b.setStep("Checking the second draft...");

    const strips = document.querySelectorAll(
      '[data-blade-surface="scan-progress"]',
    );
    expect(strips).toHaveLength(2);
    expect(
      document.getElementById(scopedSurfaceId("scan-progress", first)),
    ).not.toBeNull();
    expect(
      document.getElementById(scopedSurfaceId("scan-progress", second)),
    ).not.toBeNull();

    a.close();
    expect(
      document.querySelectorAll('[data-blade-surface="scan-progress"]'),
    ).toHaveLength(1);
    b.close();
  });

  it("keeps each compose to a single strip", () => {
    const only = compose();

    const first = showScanProgress(only, () => undefined);
    const second = showScanProgress(only, () => undefined);

    expect(
      document.querySelectorAll('[data-blade-surface="scan-progress"]'),
    ).toHaveLength(1);
    first.close();
    second.close();
  });

  it("leaves each strip its own cancel button", () => {
    const first = compose();
    const second = compose();
    const cancelFirst = vi.fn();
    const cancelSecond = vi.fn();

    showScanProgress(first, cancelFirst);
    showScanProgress(second, cancelSecond);

    const strip = document.getElementById(
      scopedSurfaceId("scan-progress", second),
    );
    strip?.querySelector("button")?.click();

    expect(cancelSecond).toHaveBeenCalledTimes(1);
    expect(cancelFirst).not.toHaveBeenCalled();
  });

  it("recognises its own surfaces", () => {
    const only = compose();
    showScanProgress(only, () => undefined);
    const strip = document.getElementById(
      scopedSurfaceId("scan-progress", only),
    );

    expect(isBladeSurface(strip?.querySelector("button") ?? null)).toBe(true);
    expect(isBladeSurface(only)).toBe(false);
  });
});

describe("attributing a hidden file input to a compose", () => {
  it("trusts the binding while the compose contains the input", () => {
    const owner = compose();
    const other = compose();
    const input = document.createElement("input");
    input.type = "file";
    owner.append(input);

    expect(boundComposeStillOwns(owner, input, other)).toBe(true);
  });

  it("trusts the binding when no other compose is active", () => {
    const owner = compose();
    const input = document.createElement("input");
    input.type = "file";
    document.body.append(input);

    expect(boundComposeStillOwns(owner, input, null)).toBe(true);
    expect(boundComposeStillOwns(owner, input, owner)).toBe(true);
  });

  it("hands a document level input to the compose in use now", () => {
    const earlier = compose();
    const later = compose();
    const input = document.createElement("input");
    input.type = "file";
    document.body.append(input);

    expect(boundComposeStillOwns(earlier, input, later)).toBe(false);
  });

  it("ignores an active compose Gmail already removed", () => {
    const earlier = compose();
    const later = compose();
    later.remove();
    const input = document.createElement("input");
    input.type = "file";
    document.body.append(input);

    expect(boundComposeStillOwns(earlier, input, later)).toBe(true);
  });
});
