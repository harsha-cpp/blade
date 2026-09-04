import { SEND_LABEL_TABLE, type SendLabelSet } from "./send-labels";

export type SendTriggerKind = "send" | "schedule-open" | "schedule-confirm";

export interface SendTrigger {
  kind: SendTriggerKind;
  element: HTMLElement;
}

export type SendShortcutOutcome =
  | { kind: "ignore" }
  | { kind: "no-send-control" }
  | { kind: "scan"; trigger: SendTrigger };

export type ComposeButtonOutcome = SendShortcutOutcome;

const ACTIONABLE_SELECTOR =
  '[role="button"],[role="menuitem"],button,[data-tooltip]';
const BUTTON_SELECTOR = '[role="button"],button';

const MAX_GROUP_DEPTH = 3;
const MAX_GROUP_ACTIONS = 4;
const MIN_STRUCTURAL_SIGNALS = 3;

function collect(pick: (set: SendLabelSet) => readonly string[]): string[] {
  return [...new Set(SEND_LABEL_TABLE.flatMap(pick).map(normalize))];
}

const SEND_LABELS = collect((set) => set.send);
const SCHEDULE_LABELS = collect((set) => set.scheduleSend);
const NON_COMMIT_LABELS = collect((set) => set.nonCommit);
const MORE_OPTIONS_LABELS = collect((set) => set.moreOptions);
const SCHEDULE_NAVIGATION_LABELS = collect((set) => set.scheduleNavigation);

const DATE_PICKER_SELECTOR = '[role="grid"],[role="listbox"]';

export function triggerLabel(el: Element): string {
  const aria = el.getAttribute("aria-label");
  if (aria?.trim()) return normalize(aria);
  const tooltip = el.getAttribute("data-tooltip");
  if (tooltip?.trim()) return normalize(tooltip);
  const text = (el.textContent ?? "").trim();
  return text.length > 0 && text.length <= 40 ? normalize(text) : "";
}

function normalize(value: string): string {
  return value
    .normalize("NFC")
    .replace(/[\u200b-\u200f\u202a-\u202e\u2066-\u2069]/g, "")
    .trim()
    .toLowerCase();
}

function isAscii(value: string): boolean {
  return !/[^\u0020-\u007e]/.test(value);
}

function isWordChar(char: string | undefined): boolean {
  return char !== undefined && /[a-z0-9]/.test(char);
}

function containsWord(label: string, entry: string): boolean {
  let index = label.indexOf(entry);
  while (index !== -1) {
    const startFree = !isWordChar(entry[0]) || !isWordChar(label[index - 1]);
    const endFree =
      !isWordChar(entry[entry.length - 1]) ||
      !isWordChar(label[index + entry.length]);
    if (startFree && endFree) return true;
    index = label.indexOf(entry, index + 1);
  }
  return false;
}

function matchesLabel(label: string, entries: readonly string[]): boolean {
  return entries.some((entry) =>
    isAscii(entry) ? containsWord(label, entry) : label.includes(entry),
  );
}

export function classifySendElement(el: Element): SendTriggerKind | null {
  const label = triggerLabel(el);
  if (!label) return null;
  if (matchesLabel(label, NON_COMMIT_LABELS)) return null;
  if (matchesLabel(label, MORE_OPTIONS_LABELS)) return null;

  const menuItem = el.getAttribute("role") === "menuitem";
  if (matchesLabel(label, SCHEDULE_LABELS)) {
    return menuItem ? "schedule-open" : "schedule-confirm";
  }
  if (matchesLabel(label, SEND_LABELS)) {
    return menuItem ? "schedule-open" : "send";
  }
  return null;
}

export function classifyScheduleDialogAction(
  el: Element,
): SendTriggerKind | null {
  const dialog = el.closest('[role="dialog"],[role="alertdialog"]');
  if (!dialog) return null;
  if (!matchesLabel(dialogName(dialog), SCHEDULE_LABELS)) return null;
  if (el.closest(DATE_PICKER_SELECTOR)) return null;

  const label = triggerLabel(el);
  if (!label) return null;
  if (matchesLabel(label, NON_COMMIT_LABELS)) return null;
  if (matchesLabel(label, SCHEDULE_NAVIGATION_LABELS)) return null;
  return "schedule-confirm";
}

function dialogName(dialog: Element): string {
  const aria = dialog.getAttribute("aria-label");
  if (aria?.trim()) return normalize(aria);
  const labelledBy = dialog.getAttribute("aria-labelledby");
  if (labelledBy) {
    const heading = dialog.ownerDocument.getElementById(labelledBy);
    if (heading?.textContent?.trim()) return normalize(heading.textContent);
  }
  const heading = dialog.querySelector('h1,h2,[role="heading"]');
  return heading?.textContent ? normalize(heading.textContent) : "";
}

export function findSendTrigger(target: Element | null): SendTrigger | null {
  let node: Element | null = target;
  for (let depth = 0; node && depth < 6; depth += 1) {
    const actionable = node.closest<HTMLElement>(ACTIONABLE_SELECTOR);
    if (!actionable) return null;
    const kind =
      classifySendElement(actionable) ??
      classifyScheduleDialogAction(actionable);
    if (kind) return { kind, element: actionable };
    node = actionable.parentElement;
  }
  return null;
}

function opensMenu(el: Element): boolean {
  if (el.getAttribute("aria-haspopup") === "true") return true;
  return matchesLabel(triggerLabel(el), MORE_OPTIONS_LABELS);
}

function isUnavailable(el: Element): boolean {
  return (
    el.getAttribute("aria-disabled") === "true" || el.hasAttribute("disabled")
  );
}

function precedes(first: Element, second: Element): boolean {
  return (
    (first.compareDocumentPosition(second) &
      Node.DOCUMENT_POSITION_FOLLOWING) !==
    0
  );
}

function actionsIn(root: Element): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(BUTTON_SELECTOR));
}

function sendGroupOf(el: HTMLElement): Element | null {
  let group: Element | null = el.parentElement;
  for (let depth = 0; group && depth < MAX_GROUP_DEPTH; depth += 1) {
    const actions = actionsIn(group);
    if (actions.length > MAX_GROUP_ACTIONS) return null;
    const menuCaret = actions.find(
      (action) => action !== el && opensMenu(action),
    );
    if (menuCaret && actions[0] === el && precedes(el, menuCaret)) return group;
    group = group.parentElement;
  }
  return null;
}

export function sendControlGroup(el: HTMLElement): Element {
  return sendGroupOf(el) ?? el;
}

function isBelowComposeBody(el: Element, compose: Element): boolean {
  const body = compose.querySelector('[role="textbox"]');
  if (!body || body.contains(el)) return false;
  return precedes(body, el);
}

function structuralSendSignals(el: HTMLElement, compose: Element): number {
  if (el.getAttribute("role") === "menuitem") return 0;
  if (opensMenu(el)) return 0;
  if (!sendGroupOf(el)) return 0;

  let signals = 1;
  if (el.hasAttribute("data-tooltip")) signals += 1;
  if (el.hasAttribute("jslog")) signals += 1;
  if (isBelowComposeBody(el, compose)) signals += 1;
  return signals;
}

function strongestByStructure(
  list: readonly HTMLElement[],
  compose: Element,
): HTMLElement | null {
  let best: HTMLElement | null = null;
  let bestSignals = -1;
  for (const el of list) {
    const signals = structuralSendSignals(el, compose);
    if (signals > bestSignals) {
      best = el;
      bestSignals = signals;
    }
  }
  return best;
}

export function findSendControl(compose: Element): HTMLElement | null {
  const candidates = Array.from(
    compose.querySelectorAll<HTMLElement>(ACTIONABLE_SELECTOR),
  ).filter(
    (el) =>
      !isUnavailable(el) &&
      !opensMenu(el) &&
      !matchesLabel(triggerLabel(el), NON_COMMIT_LABELS),
  );

  const labelled = candidates.filter(
    (el) => classifySendElement(el) === "send",
  );
  if (labelled.length > 0) return strongestByStructure(labelled, compose);

  let best: HTMLElement | null = null;
  let bestSignals = 0;
  let ambiguous = false;
  for (const candidate of candidates) {
    const signals = structuralSendSignals(candidate, compose);
    if (signals < MIN_STRUCTURAL_SIGNALS) continue;
    if (signals > bestSignals) {
      best = candidate;
      bestSignals = signals;
      ambiguous = false;
    } else if (signals === bestSignals) {
      ambiguous = true;
    }
  }
  return ambiguous ? null : best;
}

export function findSendControlTrigger(
  compose: Element,
  target: Element | null,
): SendTrigger | null {
  if (!target) return null;
  const control = findSendControl(compose);
  if (!control) return null;
  if (control !== target && !control.contains(target)) return null;
  return { kind: "send", element: control };
}

export function resolveComposeButtonActivation(
  compose: Element,
  target: Element | null,
): ComposeButtonOutcome {
  if (!target?.closest(BUTTON_SELECTOR)) return { kind: "ignore" };
  if (!findSendControl(compose)) return { kind: "no-send-control" };
  const trigger = findSendControlTrigger(compose, target);
  return trigger ? { kind: "scan", trigger } : { kind: "ignore" };
}

export function resolveSendShortcut(
  compose: Element | null,
): SendShortcutOutcome {
  if (!compose) return { kind: "ignore" };
  const element = findSendControl(compose);
  if (!element) return { kind: "no-send-control" };
  return { kind: "scan", trigger: { kind: "send", element } };
}

export interface KeyLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
}

export function isSendShortcut(event: KeyLike): boolean {
  return (
    (event.key === "Enter" || event.key === "Return") &&
    (event.ctrlKey || event.metaKey)
  );
}

export function isTextEntryTarget(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return true;
  return (
    el.closest('[contenteditable="true"],[role="textbox"],input,textarea') !==
    null
  );
}

export function isActivationKey(event: KeyLike): boolean {
  return event.key === "Enter" || event.key === " " || event.key === "Spacebar";
}
