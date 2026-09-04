import type { AttachmentUrlRef } from "./attachments";
import { extractUserEmail } from "./identity";
import { recipientAddress } from "./policy";
import { stripHtml } from "./phi";

const DRIVE_HOSTS = new Set(["drive.google.com", "docs.google.com"]);

export interface ComposeData {
  subject: string;
  body: string;
  recipients: string[];
  userEmail: string | undefined;
  attachments: File[];
  unresolvedRefs: AttachmentUrlRef[];
  driveLinks: string[];
  fingerprint: string;
}

export class ComposeReadError extends Error {
  constructor(missing: string) {
    super(`Blade could not find the ${missing} of this draft`);
    this.name = "ComposeReadError";
  }
}

export function fileKey(file: File): string {
  return `${file.name}::${file.size}::${file.lastModified}`;
}

const SUBJECT_SELECTOR = 'input[name="subjectbox"]';

const LABELLED_BODY_SELECTOR =
  '[role="textbox"][aria-label*="Body" i], [role="textbox"][g_editable="true"], div[aria-label*="Message Body" i]';

const EDITABLE_SELECTOR = '[role="textbox"],[contenteditable="true"]';

const ADDRESS_FIELD_SELECTOR =
  'input,textarea,[role="combobox"],[role="listbox"],[aria-label*="subject" i],[aria-label*="recipient" i],[aria-label*="To recipients" i]';

function isDraftBody(el: HTMLElement): boolean {
  if (el.closest(ADDRESS_FIELD_SELECTOR) !== null) return false;
  return (
    el.getAttribute("contenteditable") === "true" ||
    el.getAttribute("g_editable") === "true" ||
    el.isContentEditable === true
  );
}

export function findComposeBody(compose: Element): HTMLElement | null {
  const labelled = compose.querySelector<HTMLElement>(LABELLED_BODY_SELECTOR);
  if (labelled) return labelled;
  for (const el of compose.querySelectorAll<HTMLElement>(EDITABLE_SELECTOR)) {
    if (isDraftBody(el)) return el;
  }
  return null;
}

function isQuoted(el: Element): boolean {
  return el.closest(".gmail_quote, blockquote") !== null;
}

function isSignature(el: Element): boolean {
  return (
    el.closest(".gmail_signature, [data-smartmail='gmail_signature']") !== null
  );
}

export function extractAttachmentRefs(compose: Element): AttachmentUrlRef[] {
  const refs = new Map<string, AttachmentUrlRef>();

  compose.querySelectorAll<HTMLElement>("[download_url]").forEach((el) => {
    if (isQuoted(el)) return;
    const raw = el.getAttribute("download_url");
    if (!raw) return;
    const parts = raw.split(":");
    if (parts.length < 3) return;
    const mimeType = parts[0] || undefined;
    const name = parts[1] || undefined;
    const url = parts.slice(2).join(":");
    if (!url) return;
    refs.set(url, { url, name, mimeType });
  });

  compose
    .querySelectorAll<HTMLAnchorElement>(
      'a[href*="view=att"], a[href*="disp=att"], a[href*="realattid="]',
    )
    .forEach((a) => {
      if (isQuoted(a)) return;
      const href = a.href;
      if (!href || refs.has(href)) return;
      refs.set(href, { url: href, name: a.textContent?.trim() || undefined });
    });

  return Array.from(refs.values());
}

const ATTACHMENT_CHIP_SELECTOR = [
  "[download_url]",
  'a[href*="view=att"]',
  'a[href*="disp=att"]',
  'a[href*="realattid="]',
  '[aria-label*="attachment" i]:not([role="button"]):not(button):not([role="menuitem"])',
].join(", ");

export function countAttachmentChips(compose: Element): number {
  const chips = Array.from(
    compose.querySelectorAll<HTMLElement>(ATTACHMENT_CHIP_SELECTOR),
  ).filter((el) => !isQuoted(el));
  return chips.filter(
    (chip) => !chips.some((other) => other !== chip && other.contains(chip)),
  ).length;
}

export function isDriveUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && DRIVE_HOSTS.has(parsed.hostname);
  } catch {
    return false;
  }
}

export function extractDriveLinks(compose: Element): string[] {
  const links = new Set<string>();
  compose.querySelectorAll<HTMLAnchorElement>("a[href]").forEach((anchor) => {
    if (isQuoted(anchor) || isSignature(anchor)) return;
    const href = anchor.href;
    if (isDriveUrl(href)) links.add(href);
  });
  return [...links];
}

export function extractRecipients(compose: Element): string[] {
  const recipients: string[] = [];
  compose
    .querySelectorAll<HTMLElement>("[email], [data-hovercard-id]")
    .forEach((chip) => {
      const email =
        chip.getAttribute("email") ||
        chip.getAttribute("data-hovercard-id") ||
        "";
      if (email.includes("@")) recipients.push(email.trim());
    });

  const fields = ["to", "cc", "bcc"] as const;
  for (const field of fields) {
    compose
      .querySelectorAll<HTMLInputElement>(`input[name="${field}"]`)
      .forEach((input) => {
        if (input.value) {
          recipients.push(
            ...input.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          );
        }
      });
  }

  return [
    ...new Set(
      recipients
        .map(recipientAddress)
        .filter((address) => address.includes("@")),
    ),
  ];
}

export function extractComposeData(
  compose: Element,
  attachments: File[],
): ComposeData {
  const subjectInput =
    compose.querySelector<HTMLInputElement>(SUBJECT_SELECTOR);
  const subject = subjectInput?.value ?? "";

  const bodyEl = findComposeBody(compose);
  if (!bodyEl) throw new ComposeReadError("message body");
  const body = stripHtml(bodyEl.innerHTML);

  const recipients = extractRecipients(compose);

  const capturedNames = new Set(
    attachments.map((file) => file.name.trim().toLowerCase()),
  );
  const unresolvedRefs = extractAttachmentRefs(compose).filter(
    (ref) => !ref.name || !capturedNames.has(ref.name.trim().toLowerCase()),
  );

  const driveLinks = extractDriveLinks(compose);
  const attachmentChips = countAttachmentChips(compose);

  return {
    subject,
    body,
    recipients,
    userEmail: extractUserEmail(compose.ownerDocument, compose),
    attachments,
    unresolvedRefs,
    driveLinks,
    fingerprint: [
      subject,
      body,
      recipients.join(","),
      attachments.map(fileKey).sort().join("|"),
      unresolvedRefs
        .map((ref) => ref.url)
        .sort()
        .join("|"),
      [...driveLinks].sort().join("|"),
      String(attachmentChips),
    ].join(""),
  };
}

export interface StableScan<D, V> {
  data: D;
  verdict: V;
}

export async function scanUntilStable<D extends { fingerprint: string }, V>(
  read: () => D,
  scan: (data: D) => Promise<V>,
  attemptLimit: number,
  onDraftChanged?: () => void,
): Promise<StableScan<D, V> | null> {
  let data = read();
  for (let attempt = 1; attempt <= attemptLimit; attempt += 1) {
    const verdict = await scan(data);
    const current = read();
    if (current.fingerprint === data.fingerprint) return { data, verdict };
    onDraftChanged?.();
    data = current;
  }
  return null;
}
