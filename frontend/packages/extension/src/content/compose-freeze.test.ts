import { beforeEach, describe, expect, it } from "vitest";
import { freezeCompose, isPassiveKey } from "./compose-freeze";

function fixture(): { compose: HTMLElement; body: HTMLElement } {
  const compose = document.createElement("div");
  const body = document.createElement("div");
  body.setAttribute("role", "textbox");
  compose.append(body);
  document.body.append(compose);
  return { compose, body };
}

function keydown(key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  return new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
}

beforeEach(() => {
  document.body.replaceChildren();
});

describe("freezing a compose during a scan", () => {
  it.each(["beforeinput", "paste", "drop"])(
    "swallows %s while the scan is in flight",
    (type) => {
      const { compose, body } = fixture();
      const unfreeze = freezeCompose(compose);
      const event = new Event(type, { bubbles: true, cancelable: true });

      body.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      unfreeze();
    },
  );

  it("swallows typing but lets the caret and Escape through", () => {
    const { compose, body } = fixture();
    const unfreeze = freezeCompose(compose);
    const typed = keydown("a");
    const escape = keydown("Escape");
    const arrow = keydown("ArrowLeft");
    const copy = keydown("c", { metaKey: true });

    body.dispatchEvent(typed);
    body.dispatchEvent(escape);
    body.dispatchEvent(arrow);
    body.dispatchEvent(copy);

    expect(typed.defaultPrevented).toBe(true);
    expect(escape.defaultPrevented).toBe(false);
    expect(arrow.defaultPrevented).toBe(false);
    expect(copy.defaultPrevented).toBe(false);
    unfreeze();
  });

  it("marks the compose busy and restores it afterwards", () => {
    const { compose, body } = fixture();
    const unfreeze = freezeCompose(compose);
    expect(compose.getAttribute("aria-busy")).toBe("true");

    unfreeze();

    const event = new Event("beforeinput", { bubbles: true, cancelable: true });
    body.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    expect(compose.hasAttribute("aria-busy")).toBe(false);
  });

  it("treats a cut as an edit and a copy as passive", () => {
    expect(isPassiveKey(keydown("x", { metaKey: true }))).toBe(false);
    expect(isPassiveKey(keydown("c", { ctrlKey: true }))).toBe(true);
    expect(isPassiveKey(keydown("Backspace"))).toBe(false);
  });
});
