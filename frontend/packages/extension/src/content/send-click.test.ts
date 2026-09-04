import { beforeAll, afterAll, describe, expect, it, vi } from "vitest";

const NOTICE_SELECTOR = '[data-blade-surface="compose-notice"]';
const PROGRESS_SELECTOR = '[data-blade-surface="scan-progress"]';

function stubChrome(): void {
  vi.stubGlobal("chrome", {
    runtime: { id: "blade-test" },
    storage: {
      local: {
        get: () => Promise.resolve({}),
        set: () => Promise.resolve(),
      },
      onChanged: { addListener: () => undefined },
    },
  });
}

function composeShell(id: string, withBody = true): HTMLElement {
  const compose = document.createElement("div");
  compose.id = id;
  compose.setAttribute("role", "dialog");
  compose.innerHTML = [
    '<input name="to" />',
    '<input name="subjectbox" />',
    withBody
      ? '<div role="textbox" g_editable="true" aria-label="Message Body"></div>'
      : "",
  ].join("");
  return compose;
}

function button(id: string, label: string): HTMLElement {
  const el = document.createElement("div");
  el.id = id;
  el.setAttribute("role", "button");
  el.setAttribute("aria-label", label);
  return el;
}

function identifiableCompose(): HTMLElement {
  const compose = composeShell("compose-known");
  const footer = document.createElement("div");
  const group = document.createElement("div");
  const send = button("known-send", "Send");
  send.setAttribute("data-tooltip", "Send (Ctrl-Enter)");
  send.setAttribute("jslog", "send");
  const more = button("known-more", "More send options");
  more.setAttribute("aria-haspopup", "true");
  group.append(send, more);
  footer.append(group, button("known-discard", "Discard draft"));
  compose.append(footer);
  return compose;
}

function unreadableCompose(): HTMLElement {
  const compose = composeShell("compose-unreadable", false);
  const group = document.createElement("div");
  const send = button("unreadable-send", "Send");
  send.setAttribute("data-tooltip", "Send (Ctrl-Enter)");
  const more = button("unreadable-more", "More send options");
  more.setAttribute("aria-haspopup", "true");
  group.append(send, more);
  compose.append(group);
  return compose;
}

function unidentifiableCompose(): HTMLElement {
  const compose = composeShell("compose-unknown");
  const footer = document.createElement("div");
  for (const label of ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]) {
    footer.append(button(`unknown-${label.toLowerCase()}`, label));
  }
  compose.append(footer);
  return compose;
}

function clickOn(id: string): MouseEvent {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing ${id}`);
  const event = new MouseEvent("click", { bubbles: true, cancelable: true });
  el.dispatchEvent(event);
  return event;
}

function noticeText(): string {
  return document.querySelector(NOTICE_SELECTOR)?.textContent ?? "";
}

function clearNotices(): void {
  document.querySelectorAll(NOTICE_SELECTOR).forEach((el) => el.remove());
}

describe("clicking a button inside an instrumented compose", () => {
  beforeAll(async () => {
    stubChrome();
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    document.body.append(
      unidentifiableCompose(),
      identifiableCompose(),
      unreadableCompose(),
    );
    await import("./index");
  });

  afterAll(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("registers both composes", () => {
    expect(document.getElementById("compose-unknown")).not.toBeNull();
    expect(document.getElementById("compose-known")).not.toBeNull();
  });

  it("blocks any footer button when no send control can be identified", () => {
    clearNotices();

    const event = clickOn("unknown-beta");

    expect(event.defaultPrevented).toBe(true);
    expect(noticeText()).toContain("could not find the send button");
    expect(document.querySelector(PROGRESS_SELECTOR)).toBeNull();
  });

  it("lets Discard through when the send control is identified", () => {
    clearNotices();

    const event = clickOn("known-discard");

    expect(event.defaultPrevented).toBe(false);
    expect(document.querySelector(NOTICE_SELECTOR)).toBeNull();
    expect(document.querySelector(PROGRESS_SELECTOR)).toBeNull();
  });

  it("intercepts Send when the send control is identified", () => {
    clearNotices();

    const event = clickOn("known-send");

    expect(event.defaultPrevented).toBe(true);
    expect(document.querySelector(PROGRESS_SELECTOR)).not.toBeNull();
  });

  it("keeps one notice per compose when two are open", async () => {
    clearNotices();
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    clickOn("unknown-beta");
    clickOn("unreadable-send");
    await Promise.resolve();
    await Promise.resolve();

    expect(document.querySelectorAll(NOTICE_SELECTOR)).toHaveLength(2);
  });

  it("holds the message when the draft cannot be read", async () => {
    clearNotices();
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    const event = clickOn("unreadable-send");
    await Promise.resolve();
    await Promise.resolve();

    expect(event.defaultPrevented).toBe(true);
    expect(noticeText()).toContain("could not read this draft");
  });
});
