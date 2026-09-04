import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  composedRange,
  installInputProtection,
  textWindow,
} from "./input-protection";

const AADHAAR = "7534 7930 7460";
const KEPT = "Ward round notes. ";

function compositionEvent(type: string, data: string): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "data", { value: data });
  return event;
}

function caretAt(field: HTMLTextAreaElement, offset: number): void {
  field.setSelectionRange(offset, offset);
}

beforeEach(() => {
  document.body.replaceChildren();
});

describe("locating a committed composition", () => {
  it("uses the caret recorded when the composition started", () => {
    expect(composedRange("abcdefgh", { start: 2, end: 2 }, "cde")).toEqual({
      start: 2,
      end: 5,
    });
  });

  it("falls back to the end of the field when the caret was not seen", () => {
    expect(composedRange("abcdefgh", null, "fgh")).toEqual({
      start: 5,
      end: 8,
    });
  });

  it("never runs past the end of the field", () => {
    expect(composedRange("abc", { start: 2, end: 2 }, "wxyz")).toEqual({
      start: 2,
      end: 3,
    });
    expect(composedRange("abc", { start: 99, end: 99 }, "z")).toEqual({
      start: 3,
      end: 3,
    });
  });

  it("reads the committed text together with its context", () => {
    const text = `${KEPT}Patient Aadhaar ${AADHAAR}`;
    const window = textWindow(text, {
      start: KEPT.length,
      end: text.length,
    });

    expect(window).toContain("Patient Aadhaar");
    expect(window).toContain(AADHAAR);
  });
});

describe("Indic and other IME input", () => {
  it("removes only the composed text and keeps what was typed before", () => {
    const field = document.createElement("textarea");
    field.value = `${KEPT}Patient Aadhaar `;
    document.body.append(field);
    caretAt(field, field.value.length);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });

    field.dispatchEvent(compositionEvent("compositionstart", ""));
    field.value = `${KEPT}Patient Aadhaar ${AADHAAR}`;
    field.dispatchEvent(compositionEvent("compositionend", AADHAAR));

    expect(field.value).toBe(`${KEPT}Patient Aadhaar `);
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("leaves a safe composition alone", () => {
    const field = document.createElement("textarea");
    field.value = "Meeting notes: ";
    document.body.append(field);
    caretAt(field, field.value.length);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });

    field.dispatchEvent(compositionEvent("compositionstart", ""));
    field.value = "Meeting notes: agenda for Monday";
    field.dispatchEvent(
      compositionEvent("compositionend", "agenda for Monday"),
    );

    expect(field.value).toBe("Meeting notes: agenda for Monday");
    expect(notify).not.toHaveBeenCalled();
    dispose();
  });

  it("removes a composition committed in the middle of a field", () => {
    const tail = " please file it.";
    const field = document.createElement("textarea");
    field.value = `${KEPT}Patient Aadhaar ${tail}`;
    document.body.append(field);
    const start = `${KEPT}Patient Aadhaar `.length;
    caretAt(field, start);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });

    field.dispatchEvent(compositionEvent("compositionstart", ""));
    field.value = `${KEPT}Patient Aadhaar ${AADHAAR}${tail}`;
    field.dispatchEvent(compositionEvent("compositionend", AADHAAR));

    expect(field.value).toBe(`${KEPT}Patient Aadhaar ${tail}`);
    dispose();
  });

  it("does not wipe the field while a composition is still running", () => {
    const field = document.createElement("textarea");
    field.value = `${KEPT}Patient Aadhaar ${AADHAAR}`;
    document.body.append(field);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });

    const event = new InputEvent("input", {
      bubbles: true,
      inputType: "insertCompositionText",
      isComposing: true,
    });
    field.dispatchEvent(event);

    expect(field.value).toBe(`${KEPT}Patient Aadhaar ${AADHAAR}`);
    expect(notify).not.toHaveBeenCalled();
    dispose();
  });

  it("takes out only the composed range of a rich editor, not the whole editor", () => {
    const editor = document.createElement("div");
    editor.contentEditable = "true";
    editor.setAttribute("role", "textbox");
    editor.textContent = `${KEPT}Patient Aadhaar `;
    document.body.append(editor);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });

    const text = editor.firstChild as Text;
    const range = document.createRange();
    range.setStart(text, text.data.length);
    range.collapse(true);
    const selection = document.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);

    editor.dispatchEvent(compositionEvent("compositionstart", ""));
    text.data = `${KEPT}Patient Aadhaar ${AADHAAR}`;
    editor.dispatchEvent(compositionEvent("compositionend", AADHAAR));

    expect(editor.textContent).toBe(`${KEPT}Patient Aadhaar `);
    expect(editor.isConnected).toBe(true);
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });
});
