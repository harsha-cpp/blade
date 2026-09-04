import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { detectPhi } from "./phi";
import {
  INSERTION_WINDOW_CHARS,
  insertionCandidate,
  insertionWindow,
  installInputProtection,
} from "./input-protection";

vi.mock("./phi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./phi")>();
  return { ...actual, detectPhi: vi.fn(actual.detectPhi) };
});

const detector = vi.mocked(detectPhi);

const ABHA_HEAD = "Patient ABHA: 14-7236-8829-222";
const TAIL = " ward round notes follow here.";

function beforeInput(data: string): InputEvent {
  return new InputEvent("beforeinput", {
    bubbles: true,
    cancelable: true,
    data,
    inputType: "insertText",
  });
}

function inspectedLengths(): number[] {
  return detector.mock.calls.map((call) => String(call[0]).length);
}

function hugeField(caret: number): HTMLTextAreaElement {
  const field = document.createElement("textarea");
  field.value = "a".repeat(100_000);
  document.body.append(field);
  field.setSelectionRange(caret, caret);
  return field;
}

beforeEach(() => {
  document.body.replaceChildren();
  detector.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("checking an insertion in context", () => {
  it("checks a mid-text keystroke against the text around the caret", () => {
    const field = document.createElement("textarea");
    field.value = `${ABHA_HEAD}${TAIL}`;
    document.body.append(field);
    field.setSelectionRange(ABHA_HEAD.length, ABHA_HEAD.length);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    const event = beforeInput("6");

    field.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("places the keystroke at the caret in a rich editor, not at the end", () => {
    const editor = document.createElement("div");
    editor.contentEditable = "true";
    editor.setAttribute("role", "textbox");
    editor.textContent = `${ABHA_HEAD}${TAIL}`;
    document.body.append(editor);

    const text = editor.firstChild as Text;
    const range = document.createRange();
    range.setStart(text, ABHA_HEAD.length);
    range.collapse(true);
    const selection = document.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);

    expect(insertionCandidate(editor, "6")).toBe(`${ABHA_HEAD}6${TAIL}`);
    expect(insertionWindow(editor, "6")).toContain("14-7236-8829-2226");

    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    const event = beforeInput("6");

    editor.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    dispose();
  });
});

describe("clinical context beyond the inspection window", () => {
  it("blocks a typed phone number 1,500 chars after the word patient", () => {
    const field = document.createElement("textarea");
    const lead = `Patient notes: ${"x".repeat(1_500)}`;
    field.value = `${lead}call `;
    document.body.append(field);
    field.setSelectionRange(field.value.length, field.value.length);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    const event = beforeInput("9876543210");

    field.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(notify).toHaveBeenCalledOnce();
    dispose();
  });

  it("leaves a phone number in an ordinary field alone", () => {
    const field = document.createElement("textarea");
    field.value = `Order update: ${"x".repeat(1_500)}call `;
    document.body.append(field);
    field.setSelectionRange(field.value.length, field.value.length);
    const notify = vi.fn();
    const dispose = installInputProtection({ document, notify });
    const event = beforeInput("9876543210");

    field.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(notify).not.toHaveBeenCalled();
    dispose();
  });

  it("still inspects only the window around the caret", () => {
    const field = document.createElement("textarea");
    field.value = `Patient notes: ${"x".repeat(50_000)}call `;
    document.body.append(field);
    field.setSelectionRange(field.value.length, field.value.length);
    const dispose = installInputProtection({ document, notify: vi.fn() });

    field.dispatchEvent(beforeInput("9876543210"));

    for (const length of inspectedLengths()) {
      expect(length).toBeLessThanOrEqual(INSERTION_WINDOW_CHARS + 10);
    }
    dispose();
  });
});

describe("per keystroke cost", () => {
  it("inspects no more than a window around the caret of a 100 kB field", () => {
    const field = hugeField(50_000);
    const dispose = installInputProtection({ document, notify: vi.fn() });

    field.dispatchEvent(beforeInput("6"));
    field.dispatchEvent(new InputEvent("input", { bubbles: true }));

    const lengths = inspectedLengths();
    expect(lengths.length).toBeGreaterThan(0);
    for (const length of lengths) {
      expect(length).toBeLessThanOrEqual(INSERTION_WINDOW_CHARS + 1);
    }
    dispose();
  });

  it("does not scan the field twice for one keystroke", () => {
    const field = hugeField(50_000);
    const dispose = installInputProtection({ document, notify: vi.fn() });

    field.dispatchEvent(beforeInput("6"));
    field.dispatchEvent(new InputEvent("input", { bubbles: true }));

    expect(detector).toHaveBeenCalledOnce();
    dispose();
  });

  it("still reads the whole field when nothing checked the edit", () => {
    const field = hugeField(50_000);
    const dispose = installInputProtection({ document, notify: vi.fn() });

    field.dispatchEvent(new InputEvent("input", { bubbles: true }));

    expect(inspectedLengths()).toEqual([100_000]);
    dispose();
  });

  it("still reads the whole field for a paste", () => {
    const field = hugeField(50_000);
    const dispose = installInputProtection({ document, notify: vi.fn() });
    const paste = new Event("paste", {
      bubbles: true,
      cancelable: true,
    }) as ClipboardEvent;
    Object.defineProperty(paste, "clipboardData", {
      value: { getData: () => "pasted text" },
    });

    field.dispatchEvent(paste);

    expect(Math.max(...inspectedLengths())).toBeGreaterThanOrEqual(100_000);
    dispose();
  });
});
