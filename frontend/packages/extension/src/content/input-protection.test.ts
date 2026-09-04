import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  insertedRange,
  insertionCandidate,
  inspectProtectedText,
  installInputProtection,
  isProtectedEditable,
} from "./input-protection";

function pasteEvent(text: string): ClipboardEvent {
  const event = new Event("paste", {
    bubbles: true,
    cancelable: true,
  }) as ClipboardEvent;
  Object.defineProperty(event, "clipboardData", {
    value: { getData: (type: string) => (type === "text/plain" ? text : "") },
  });
  return event;
}

function trusted<T extends Event>(event: T): T {
  Object.defineProperty(event, "isTrusted", { value: true });
  return event;
}

const AADHAAR = "7534 7930 7460";

describe("universal input protection", () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("blocks patient identifiers without retaining the raw value", () => {
    const decision = inspectProtectedText(
      `Patient Aadhaar ${AADHAAR}, UHID 0024518`,
    );

    expect(decision.blocked).toBe(true);
    expect(decision.reason).toBe("sensitive-data");
    expect(JSON.stringify(decision)).not.toContain("7534 7930 7460");
    expect(JSON.stringify(decision)).not.toContain("0024518");
  });

  it.each([
    ["ABHA number", "Patient ABHA 14-7236-8829-2226"],
    ["diagnosis code", "Patient diagnosis: post COVID-19 condition U09.9"],
    ["date of birth", "Patient DOB: 14/08/1971"],
    ["lab accession", "Patient specimen LAB-2026-0091185 received"],
  ])("blocks a %s", (_label, text) => {
    expect(inspectProtectedText(text).blocked).toBe(true);
  });

  it.each([
    ["an email address", "admin@sunrisehospital.in"],
    ["a person name", "Ramesh Kumar Iyer"],
    ["a date of birth", "14/08/1971"],
    ["a phone number", "98450 12345"],
    ["an email and a name together", "Ramesh Kumar Iyer ramesh@example.in"],
  ])("does not block %s on its own", (_label, text) => {
    expect(inspectProtectedText(text).blocked).toBe(false);
  });

  it.each([
    ["clinical wording", "Patient email: ramesh.iyer88@gmail.com"],
    ["a standalone identifier alongside", `Ramesh Kumar Iyer ${AADHAAR}`],
  ])("blocks a contextual identifier given %s", (_label, text) => {
    expect(inspectProtectedText(text).blocked).toBe(true);
  });

  it("does not block a standalone phone number without clinical context", () => {
    expect(inspectProtectedText("Call me at 98450 12345").blocked).toBe(false);
    expect(inspectProtectedText("Patient phone: 98450 12345").blocked).toBe(
      true,
    );
  });

  it("leaves ordinary prose alone", () => {
    expect(
      inspectProtectedText("Please summarise the public meeting notes.")
        .blocked,
    ).toBe(false);
    expect(
      inspectProtectedText("Ward B12 needs vitamin B12 stock delivered.")
        .blocked,
    ).toBe(false);
  });

  it("refuses to inspect text beyond the size limit rather than letting it through", () => {
    const decision = inspectProtectedText("a".repeat(500_001));

    expect(decision.blocked).toBe(true);
    expect(decision.reason).toBe("inspection-limit");
    expect(decision.hits).toEqual([]);
  });

  it("builds the insertion candidate at the current selection", () => {
    const input = document.createElement("input");
    input.value = "ABHA: 14-7236-8829-222";
    input.setSelectionRange(input.value.length, input.value.length);

    expect(insertionCandidate(input, "6")).toBe("ABHA: 14-7236-8829-2226");
  });

  it("blocks an unsafe paste before the browser inserts it", () => {
    const input = document.createElement("textarea");
    const notify = vi.fn();
    document.body.append(input);
    const dispose = installInputProtection({ document, notify });
    const event = pasteEvent(`Patient Aadhaar ${AADHAAR}`);

    input.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(input.value).toBe("");
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("never inspects a password field", () => {
    const text = document.createElement("textarea");
    const password = document.createElement("input");
    password.type = "password";
    document.body.append(text, password);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    const safeEvent = pasteEvent("Please summarise the public meeting notes.");
    const passwordEvent = pasteEvent(`Patient Aadhaar ${AADHAAR}`);

    text.dispatchEvent(safeEvent);
    password.dispatchEvent(passwordEvent);

    expect(safeEvent.defaultPrevented).toBe(false);
    expect(passwordEvent.defaultPrevented).toBe(false);
    expect(isProtectedEditable(password)).toBe(false);
    expect(notify).not.toHaveBeenCalled();
    dispose();
  });

  it("blocks the final typed character that completes an identifier", () => {
    const input = document.createElement("input");
    input.value = "Patient ABHA: 14-7236-8829-222";
    input.setSelectionRange(input.value.length, input.value.length);
    document.body.append(input);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    const event = new InputEvent("beforeinput", {
      bubbles: true,
      cancelable: true,
      data: "6",
      inputType: "insertText",
    });

    input.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("clears content introduced by autofill or a page script", () => {
    const input = document.createElement("textarea");
    document.body.append(input);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    input.value = "Patient DOB: 14/08/1971";

    input.dispatchEvent(new Event("input", { bubbles: true }));

    expect(input.value).toBe("");
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("blocks form submission when a field was populated without input events", () => {
    const form = document.createElement("form");
    const input = document.createElement("textarea");
    input.value = "Patient DOB: 14/08/1971";
    form.append(input);
    document.body.append(form);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    const event = new SubmitEvent("submit", {
      bubbles: true,
      cancelable: true,
    });

    form.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("blocks a non-form send action using the last focused editor", () => {
    const input = document.createElement("div");
    input.contentEditable = "true";
    input.setAttribute("role", "textbox");
    const button = document.createElement("button");
    button.textContent = "Send";
    document.body.append(input, button);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    input.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    input.textContent = "Patient DOB: 14/08/1971";
    const event = new MouseEvent("click", { bubbles: true, cancelable: true });

    button.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("blocks a page dispatched insertion but never reports it", () => {
    const input = document.createElement("textarea");
    document.body.append(input);
    const notify = vi.fn();
    const onBlocked = vi.fn();
    const dispose = installInputProtection({ document, notify, onBlocked });
    const event = new InputEvent("beforeinput", {
      bubbles: true,
      cancelable: true,
      data: `Aadhaar ${AADHAAR}`,
      inputType: "insertText",
    });

    input.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(input.value).toBe("");
    expect(notify).toHaveBeenCalledWith(expect.anything(), false);
    expect(onBlocked).not.toHaveBeenCalled();
    dispose();
  });

  it("reports a block that a trusted browser event caused", () => {
    const input = document.createElement("textarea");
    document.body.append(input);
    const notify = vi.fn();
    const onBlocked = vi.fn();
    const dispose = installInputProtection({ document, notify, onBlocked });
    const event = trusted(
      new InputEvent("beforeinput", {
        bubbles: true,
        cancelable: true,
        data: `Aadhaar ${AADHAAR}`,
        inputType: "insertText",
      }),
    );

    input.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(notify).toHaveBeenCalledWith(expect.anything(), true);
    expect(onBlocked).toHaveBeenCalledOnce();
    dispose();
  });

  it.each([
    [
      "a page dispatched paste",
      () => pasteEvent(`Patient Aadhaar ${AADHAAR}`),
      false,
    ],
    [
      "a page dispatched input",
      () => new Event("input", { bubbles: true, cancelable: true }),
      false,
    ],
    [
      "a real paste",
      () => trusted(pasteEvent(`Patient Aadhaar ${AADHAAR}`)),
      true,
    ],
    [
      "autofill",
      () => trusted(new Event("input", { bubbles: true, cancelable: true })),
      true,
    ],
  ])("reports %s only when it is trusted", (_label, build, reported) => {
    const input = document.createElement("textarea");
    document.body.append(input);
    const onBlocked = vi.fn();
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify, onBlocked });
    input.value = `Patient Aadhaar ${AADHAAR}`;

    input.dispatchEvent(build());

    expect(notify).toHaveBeenCalledOnce();
    expect(onBlocked).toHaveBeenCalledTimes(reported ? 1 : 0);
    dispose();
  });

  it("removes every listener when disposed", () => {
    const input = document.createElement("textarea");
    document.body.append(input);
    const notify = vi.fn();
    installInputProtection({ document, notify })();
    const event = pasteEvent(`Patient Aadhaar ${AADHAAR}`);

    input.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(notify).not.toHaveBeenCalled();
  });
});

describe("locating what an autofill just inserted", () => {
  it("spans the inserted text ending at the caret", () => {
    expect(
      insertedRange("Call 9876543210", { start: 15, end: 15 }, "9876543210"),
    ).toEqual({ start: 5, end: 15 });
  });

  it("gives up when the text does not match the caret", () => {
    expect(
      insertedRange("Call 9876543210", { start: 9, end: 9 }, "9876543210"),
    ).toBeNull();
    expect(
      insertedRange("short", { start: 5, end: 5 }, "much longer"),
    ).toBeNull();
    expect(insertedRange("Call", { start: 4, end: 4 }, "")).toBeNull();
  });
});

describe("what an autofill backstop takes out of the field", () => {
  beforeEach(() => {
    document.body.replaceChildren();
  });

  it("removes only the inserted value and keeps the rest of the field", () => {
    const field = document.createElement("textarea");
    const kept = "Patient follow up. Call ";
    field.value = `${kept}7534 7930 7460`;
    document.body.append(field);
    field.setSelectionRange(field.value.length, field.value.length);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });

    const event = new InputEvent("input", {
      bubbles: true,
      data: "7534 7930 7460",
      inputType: "insertReplacementText",
    });
    field.dispatchEvent(event);

    expect(field.value).toBe(kept);
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("still clears the field when it cannot place the insertion", () => {
    const field = document.createElement("textarea");
    field.value = "Patient Aadhaar 7534 7930 7460";
    document.body.append(field);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });

    field.dispatchEvent(new InputEvent("input", { bubbles: true }));

    expect(field.value).toBe("");
    dispose();
  });

  it("clears the field when removing the insertion is not enough", () => {
    const field = document.createElement("textarea");
    field.value = "Patient Aadhaar 7534 7930 7460 and MRN 0024518";
    document.body.append(field);
    field.setSelectionRange(field.value.length, field.value.length);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });

    field.dispatchEvent(
      new InputEvent("input", {
        bubbles: true,
        data: " and MRN 0024518",
        inputType: "insertReplacementText",
      }),
    );

    expect(field.value).toBe("");
    dispose();
  });
});
