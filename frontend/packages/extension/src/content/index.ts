import { mountWarningModal } from "../modal/mount";
import {
  attachmentUploadResultSchema,
  type AttachmentUploadResult,
  type Verdict,
} from "@bladedlp/shared";
import { detectPhi, type PhiHit } from "./phi";
import { fetchAttachmentRefs, scanAttachments } from "./attachments";
import {
  ComposeReadError,
  extractComposeData,
  fileKey,
  scanUntilStable,
  type ComposeData,
} from "./compose";
import { freezeCompose } from "./compose-freeze";
import {
  BLADE_SURFACE_ATTRIBUTE,
  boundComposeStillOwns,
  COMPOSE_NOTICE_SURFACE,
  scopedSurfaceId,
} from "./compose-scope";
import { claimInjection, GMAIL_GUARD_MARKER } from "./injection-marker";
import { parseVerdict } from "./verdict";
import {
  isContextInvalidatedError,
  isExtensionContextValid,
  showContextInvalidatedBanner,
} from "./extension-context";
import {
  attachmentStep,
  showScanProgress,
  type ScanProgress,
} from "./progress";
import { FONT_MONO, FONT_UI, palette } from "./theme";
import {
  buildLocalVerdict,
  degradedVerdict,
  emptyPolicy,
  withUnscannedAttachments,
  type OrgPolicy,
} from "./policy";
import {
  findSendControl,
  findSendTrigger,
  isActivationKey,
  isSendShortcut,
  isTextEntryTarget,
  resolveComposeButtonActivation,
  resolveSendShortcut,
  sendControlGroup,
  type SendTrigger,
  type SendTriggerKind,
} from "./send-paths";
import { isConfigFresh } from "../config-freshness";
import {
  ATTACHMENT_FETCH_TIMEOUT_MS,
  ATTACHMENT_POLL_INTERVAL_MS,
  ATTACHMENT_POLL_REQUEST_TIMEOUT_MS,
  ATTACHMENT_POLL_TIMEOUT_MS,
  ATTACHMENT_UPLOAD_TIMEOUT_MS,
  FINALIZE_TIMEOUT_MS,
  SCAN_BUDGET_MS,
  VERDICT_CACHE_TTL_MS,
} from "../config";
import {
  backendFailureStep,
  backendFailureSummary,
  BackendRequestError,
  isAttachmentRejected,
  retryDelayMs,
  sendBackendRequest,
  sendBackendRequestAndForget,
} from "../backend-client";
import {
  ATTACHMENT_INLINE_MAX_BYTES,
  BACKEND_REQUEST_MESSAGE,
  encodeBase64,
  EVENT_MAX_ENTITIES,
} from "../messages";

let orgCode: string | null = null;
let policy: OrgPolicy = emptyPolicy();

interface CachedDomain {
  domain: string;
}

interface CachedConfig {
  org_code?: string;
  domains?: CachedDomain[];
  blocked_domains?: CachedDomain[];
  fail_open?: boolean;
  fetched_at?: number;
}

function buildPolicy(
  code: string | null,
  config: CachedConfig | undefined,
): OrgPolicy {
  const next = emptyPolicy();
  if (!config) return next;
  if (!isConfigFresh(config.fetched_at)) return next;
  if (code && config.org_code && config.org_code.trim().toUpperCase() !== code)
    return next;

  for (const entry of config.domains ?? []) {
    const value = entry?.domain?.trim().toLowerCase();
    if (!value) continue;
    if (value.includes("@")) next.approvedEmails.add(value);
    else next.approvedDomains.add(value);
  }
  for (const entry of config.blocked_domains ?? []) {
    const value = entry?.domain?.trim().toLowerCase();
    if (value) next.blockedDomains.add(value);
  }
  next.failOpen = config.fail_open === true;
  next.hasConfig = true;
  return next;
}

async function loadOrgState(): Promise<void> {
  const result = await chrome.storage.local.get([
    "blade_org_code",
    "blade_config",
  ]);
  orgCode =
    (result.blade_org_code as string | undefined)?.trim().toUpperCase() ?? null;
  policy = buildPolicy(
    orgCode,
    result.blade_config as CachedConfig | undefined,
  );
}

function onStoredStateChanged(
  changes: { [key: string]: chrome.storage.StorageChange },
  area: chrome.storage.AreaName,
): void {
  if (area !== "local") return;
  if (changes.blade_org_code) {
    orgCode =
      (changes.blade_org_code.newValue as string | undefined)
        ?.trim()
        .toUpperCase() ?? null;
    policy = emptyPolicy();
    void loadOrgState();
  }
  if (changes.blade_config) {
    policy = buildPolicy(
      orgCode,
      changes.blade_config.newValue as CachedConfig | undefined,
    );
  }
}

function styled<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  css: string,
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  el.style.cssText = css;
  return el;
}

function showOrgCodeBanner(): void {
  if (document.getElementById("blade-org-banner")) return;
  const p = palette();

  const banner = styled(
    "div",
    `position:fixed;bottom:24px;right:24px;z-index:2147483646;background:${p.surface};color:${p.ink};padding:16px 18px 18px;display:flex;flex-direction:column;gap:10px;font-family:${FONT_UI};font-size:13px;line-height:1.45;width:336px;border:1px solid ${p.rule};border-top:3px solid ${p.accent};border-radius:6px;box-shadow:${p.shadow};`,
  );
  banner.id = "blade-org-banner";
  banner.setAttribute("role", "dialog");
  banner.setAttribute("aria-label", "Link this install to your organization");

  const header = styled(
    "div",
    "display:flex;align-items:baseline;justify-content:space-between;gap:8px;",
  );
  const brand = styled(
    "span",
    "display:inline-flex;align-items:baseline;gap:7px;",
  );
  const word = styled(
    "span",
    `font-family:${FONT_MONO};font-weight:600;font-size:15px;line-height:1;letter-spacing:0.09em;text-transform:uppercase;color:${p.ink};`,
  );
  word.textContent = "Blade";
  brand.append(word);
  const skip = styled(
    "button",
    `background:transparent;border:none;color:${p.ink3};font-size:12px;cursor:pointer;line-height:1;padding:2px 4px;font-family:inherit;`,
  );
  skip.type = "button";
  skip.textContent = "Dismiss";
  header.append(brand, skip);

  const description = styled("div", `color:${p.ink2};`);
  description.textContent =
    "Link this install to your organization so Blade can check recipients. Until then, messages with patient data are held for review.";

  const input = styled(
    "input",
    `height:34px;padding:0 11px;border-radius:4px;border:1px solid ${p.ruleStrong};background:${p.surface};color:${p.ink};font-size:12.5px;font-family:${FONT_MONO};letter-spacing:0.05em;text-transform:uppercase;outline:none;width:100%;box-sizing:border-box;`,
  );
  input.type = "text";
  input.placeholder = "BLD-XXXXXX";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.setAttribute("aria-label", "Organization code");

  const submit = styled(
    "button",
    `height:34px;padding:0 14px;border-radius:4px;border:1px solid ${p.accent};background:${p.accent};color:${p.accentInk};font-family:inherit;font-weight:500;font-size:13px;cursor:pointer;`,
  );
  submit.type = "button";
  submit.textContent = "Link organization";

  banner.append(header, description, input, submit);
  document.body.appendChild(banner);

  input.addEventListener("focus", () => {
    input.style.borderColor = p.accent;
  });
  input.addEventListener("blur", () => {
    input.style.borderColor = p.ruleStrong;
  });

  submit.addEventListener("click", () => {
    const code = input.value.trim().toUpperCase();
    if (code.length < 4) {
      input.style.borderColor = p.stop;
      input.focus();
      return;
    }
    void chrome.storage.local
      .set({ blade_org_code: code, blade_org_skipped: false })
      .then(() => banner.remove());
  });

  skip.addEventListener("click", () => {
    void chrome.storage.local
      .set({ blade_org_skipped: true })
      .then(() => banner.remove());
  });
}

function isLinked(): boolean {
  return orgCode !== null || policy.hasConfig;
}

async function maybeShowOrgBanner(): Promise<void> {
  if (isLinked()) return;
  const result = await chrome.storage.local.get("blade_org_skipped");
  if (result.blade_org_skipped) return;
  setTimeout(showOrgCodeBanner, 1500);
}

function reportContextLoss(err: unknown): void {
  if (isContextInvalidatedError(err) || !isExtensionContextValid()) {
    showContextInvalidatedBanner(document);
  }
}

function rememberUserEmail(email: string | undefined): string | undefined {
  if (email) void chrome.storage.local.set({ blade_last_user_email: email });
  return email;
}

function reportEvent(
  verdict: Verdict,
  userEmail: string | undefined,
  recipients: string[],
): void {
  if (!isLinked()) return;

  sendBackendRequestAndForget({
    type: BACKEND_REQUEST_MESSAGE,
    kind: "event",
    timeoutMs: FINALIZE_TIMEOUT_MS,
    clientEventId: verdict.scan_id,
    userEmail: userEmail ?? null,
    action: verdict.action,
    severity: verdict.severity,
    riskScore: verdict.risk_score,
    entities: verdict.entities.slice(0, EVENT_MAX_ENTITIES).map((e) => ({
      type: e.type,
      confidence: e.confidence,
    })),
    recipients,
  });
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

function parseAttachmentResult(body: unknown): AttachmentUploadResult {
  const parsed = attachmentUploadResultSchema.safeParse(body);
  if (!parsed.success) {
    throw new BackendRequestError(
      "http",
      undefined,
      "the server sent an attachment result Blade could not read",
    );
  }
  return parsed.data;
}

async function uploadAttachmentForBackend(
  clientScanId: string,
  file: File,
  signal: AbortSignal,
): Promise<AttachmentUploadResult | null> {
  const dataBase64 = encodeBase64(new Uint8Array(await file.arrayBuffer()));
  try {
    const body = await sendBackendRequest<unknown>(
      {
        type: BACKEND_REQUEST_MESSAGE,
        kind: "attachment-upload",
        timeoutMs: ATTACHMENT_UPLOAD_TIMEOUT_MS,
        clientScanId,
        attachmentId: crypto.randomUUID(),
        filename: file.name || "attachment",
        mimeType: file.type || "application/octet-stream",
        dataBase64,
      },
      signal,
    );
    return parseAttachmentResult(body);
  } catch (err) {
    if (isAttachmentRejected(err)) return null;
    throw err;
  }
}

async function pollAttachmentScan(
  attachmentScanId: string,
  signal: AbortSignal,
): Promise<AttachmentUploadResult> {
  const deadline = Date.now() + ATTACHMENT_POLL_TIMEOUT_MS;
  let latest: AttachmentUploadResult | null = null;
  while (Date.now() < deadline && !signal.aborted) {
    let waitMs = ATTACHMENT_POLL_INTERVAL_MS;
    try {
      latest = parseAttachmentResult(
        await sendBackendRequest<unknown>(
          {
            type: BACKEND_REQUEST_MESSAGE,
            kind: "attachment-status",
            timeoutMs: ATTACHMENT_POLL_REQUEST_TIMEOUT_MS,
            attachmentScanId,
          },
          signal,
        ),
      );
      if (latest.status !== "queued") return latest;
    } catch (err) {
      const retry = retryDelayMs(err);
      if (retry === null) throw err;
      waitMs = Math.min(retry, ATTACHMENT_POLL_TIMEOUT_MS);
    }
    await sleep(waitMs, signal);
  }
  return latest ?? { attachment_scan_id: attachmentScanId, status: "queued" };
}

async function finalizeWithBackend(
  payload: {
    clientScanId: string;
    subject: string;
    body: string;
    recipients: string[];
    userEmail: string | undefined;
    attachmentScanIds: string[];
  },
  signal: AbortSignal,
): Promise<Verdict> {
  const body = await sendBackendRequest<unknown>(
    {
      type: BACKEND_REQUEST_MESSAGE,
      kind: "scan-finalize",
      timeoutMs: FINALIZE_TIMEOUT_MS,
      clientScanId: payload.clientScanId,
      subject: payload.subject,
      body: payload.body,
      recipients: payload.recipients,
      userEmail: payload.userEmail ?? null,
      attachmentScanIds: payload.attachmentScanIds,
    },
    signal,
  );

  const verdict = parseVerdict(body);
  if (!verdict) {
    throw new BackendRequestError(
      "http",
      undefined,
      "the server sent a verdict Blade could not read",
    );
  }
  return verdict;
}

interface BackendScan {
  verdict: Verdict;
  rejectedAttachments: number;
}

async function scanWithBackend(
  input: {
    subject: string;
    body: string;
    recipients: string[];
    userEmail: string | undefined;
    attachments: File[];
  },
  signal: AbortSignal,
  progress: ScanProgress,
): Promise<BackendScan> {
  if (!isLinked()) throw new Error("this install is not linked");
  const clientScanId = crypto.randomUUID();

  const total = input.attachments.length;
  let done = 0;
  if (total > 0) progress.setStep(attachmentStep(0, total));

  const uploaded = await Promise.all(
    input.attachments.map(async (file) => {
      const result = await uploadAttachmentForBackend(
        clientScanId,
        file,
        signal,
      );
      done += 1;
      progress.setStep(attachmentStep(done, total));
      return result;
    }),
  );

  const accepted = uploaded.filter(
    (scan): scan is AttachmentUploadResult => scan !== null,
  );

  const resolved = await Promise.all(
    accepted.map((scan) =>
      scan.status === "queued"
        ? pollAttachmentScan(scan.attachment_scan_id, signal)
        : Promise.resolve(scan),
    ),
  );

  progress.setStep("Checking against your organization policy...");
  const verdict = await finalizeWithBackend(
    {
      clientScanId,
      subject: input.subject,
      body: input.body,
      recipients: input.recipients,
      userEmail: input.userEmail,
      attachmentScanIds: resolved.map((scan) => scan.attachment_scan_id),
    },
    signal,
  );
  return { verdict, rejectedAttachments: uploaded.length - accepted.length };
}

async function buildLocalFallbackVerdict(
  data: ComposeData,
  files: File[],
  unreachableAttachments: number,
): Promise<Verdict> {
  const sweep = await scanAttachments(files);
  const entities: PhiHit[] = [
    ...detectPhi(`${data.subject}\n${data.body}`, "body"),
    ...sweep.entities,
  ];
  return degradedVerdict(
    buildLocalVerdict({
      entities,
      recipients: data.recipients,
      policy,
      unscannedAttachments: unreachableAttachments + sweep.unscanned,
    }),
  );
}

const instrumentedComposes = new WeakSet<Element>();
const composeRegistry = new Set<Element>();
const composeAttachments = new WeakMap<Element, Map<string, File>>();
const fileInputCompose = new WeakMap<HTMLInputElement, Element>();
const pendingScans = new WeakMap<Element, AbortController>();
const verdictCache = new WeakMap<
  Element,
  { fingerprint: string; verdict: Verdict; at: number }
>();
let bypassing = false;

let activeCompose: Element | null = null;

function connectedComposes(): Element[] {
  for (const compose of [...composeRegistry]) {
    if (!compose.isConnected) {
      composeRegistry.delete(compose);
      if (activeCompose === compose) activeCompose = null;
    }
  }
  return [...composeRegistry];
}

function findComposeAncestor(node: Element | null): Element | null {
  if (!node) return null;
  for (const compose of connectedComposes()) {
    if (compose === node || compose.contains(node)) return compose;
  }
  return null;
}

function resolveComposeFromContext(node?: Element | null): Element | null {
  const inside = findComposeAncestor(node ?? null);
  if (inside) return inside;
  if (activeCompose?.isConnected) return activeCompose;
  const open = connectedComposes();
  return open.length === 1 ? (open[0] ?? null) : null;
}

function resolveComposeForTrigger(
  kind: SendTriggerKind,
  element: Element,
): Element | null {
  const inside = findComposeAncestor(element);
  if (inside) return inside;
  if (kind === "send") return null;
  return resolveComposeFromContext(null);
}

function getAttachmentMap(compose: Element): Map<string, File> {
  let map = composeAttachments.get(compose);
  if (!map) {
    map = new Map();
    composeAttachments.set(compose, map);
  }
  return map;
}

function captureFiles(compose: Element, files: FileList | File[] | null): void {
  if (!files || pendingScans.has(compose)) return;
  const map = getAttachmentMap(compose);
  for (const file of Array.from(files)) map.set(fileKey(file), file);
}

function readCompose(compose: Element): ComposeData {
  const data = extractComposeData(
    compose,
    Array.from(getAttachmentMap(compose).values()),
  );
  rememberUserEmail(data.userEmail);
  return data;
}

function allowSend(trigger: SendTrigger, compose: Element): void {
  const element = trigger.element.isConnected
    ? trigger.element
    : trigger.kind === "send"
      ? findSendControl(compose)
      : null;

  if (!element) {
    console.warn("[Blade] cleared control is gone; click again to send");
    showComposeNotice(compose, SEND_CONTROL_MOVED_NOTICE_TEXT);
    return;
  }

  bypassing = true;
  try {
    element.click();
  } finally {
    bypassing = false;
  }
}

const SCAN_ATTEMPT_LIMIT = 3;

async function inspectDraft(
  data: ComposeData,
  progress: ScanProgress,
  signal: AbortSignal,
  isCancelled: () => boolean,
): Promise<Verdict> {
  let files = data.attachments;
  let unscanned = data.driveLinks.length;
  if (data.unresolvedRefs.length > 0) {
    progress.setStep("Reading attachments...");
    const fetched = await fetchAttachmentRefs(
      data.unresolvedRefs,
      ATTACHMENT_FETCH_TIMEOUT_MS,
      signal,
    );
    files = [...files, ...fetched.files];
    unscanned += fetched.failed;
  }

  const uploadable = files.filter(
    (file) => file.size <= ATTACHMENT_INLINE_MAX_BYTES,
  );
  const tooLargeToUpload = files.length - uploadable.length;

  try {
    const scan = await scanWithBackend(
      {
        subject: data.subject,
        body: data.body,
        recipients: data.recipients,
        userEmail: data.userEmail,
        attachments: uploadable,
      },
      signal,
      progress,
    );
    return withUnscannedAttachments(
      scan.verdict,
      unscanned + tooLargeToUpload + scan.rejectedAttachments,
    );
  } catch (err) {
    if (isCancelled()) throw err;
    reportContextLoss(err);
    console.warn(
      "[Blade] backend scan unavailable, deciding locally:",
      backendFailureSummary(err),
    );
    progress.setStep(backendFailureStep(err));
    const verdict = await buildLocalFallbackVerdict(
      data,
      files,
      unscanned + tooLargeToUpload,
    );
    reportEvent(verdict, data.userEmail, data.recipients);
    return verdict;
  }
}

function sendVerified(
  compose: Element,
  trigger: SendTrigger,
  fingerprint: string,
): void {
  let current: ComposeData;
  try {
    current = readCompose(compose);
  } catch (err) {
    console.error(
      "[Blade] the draft could not be reread; nothing was sent",
      err,
    );
    showComposeNotice(compose, scanFailureNoticeText(err));
    return;
  }

  if (current.fingerprint === fingerprint) {
    allowSend(trigger, compose);
    return;
  }
  console.warn("[Blade] the draft changed after it was checked; rechecking");
  showComposeNotice(compose, DRAFT_CHANGED_NOTICE_TEXT);
  startSendIntercept(compose, trigger);
}

async function handleSendIntercept(
  compose: Element,
  trigger: SendTrigger,
): Promise<void> {
  if (pendingScans.has(compose)) return;
  if (!isExtensionContextValid()) showContextInvalidatedBanner(document);

  const initial = readCompose(compose);

  const cached = verdictCache.get(compose);
  if (
    cached &&
    cached.fingerprint === initial.fingerprint &&
    Date.now() - cached.at <= VERDICT_CACHE_TTL_MS
  ) {
    allowSend(trigger, compose);
    return;
  }
  verdictCache.delete(compose);

  const controller = new AbortController();
  pendingScans.set(compose, controller);
  let cancelled = false;
  const progress = showScanProgress(compose, () => {
    cancelled = true;
    controller.abort();
  });
  const unfreeze = freezeCompose(
    compose,
    trigger.element.isConnected ? sendControlGroup(trigger.element) : null,
  );
  const budget = setTimeout(() => controller.abort(), SCAN_BUDGET_MS);

  let settled: { data: ComposeData; verdict: Verdict } | null = null;
  try {
    settled = await scanUntilStable(
      () => readCompose(compose),
      (data) =>
        inspectDraft(data, progress, controller.signal, () => cancelled),
      SCAN_ATTEMPT_LIMIT,
      () =>
        progress.setStep(
          "The draft changed while it was checked. Checking again...",
        ),
    );
  } catch (err) {
    if (!cancelled) throw err;
  } finally {
    clearTimeout(budget);
    progress.close();
    unfreeze();
    pendingScans.delete(compose);
  }

  if (cancelled) return;

  if (!settled) {
    console.warn("[Blade] the draft kept changing during the scan; not sent");
    showComposeNotice(compose, DRAFT_UNSTABLE_NOTICE_TEXT);
    return;
  }

  const { data, verdict } = settled;

  if (verdict.action === "allow") {
    verdictCache.set(compose, {
      fingerprint: data.fingerprint,
      verdict,
      at: Date.now(),
    });
    allowSend(trigger, compose);
    return;
  }

  mountWarningModal(
    {
      getElement: () => compose as HTMLElement,
      send: () => sendVerified(compose, trigger, data.fingerprint),
    },
    verdict,
  );
}

const COMPOSE_NOTICE_MS = 10000;
const SEND_CONTROL_NOTICE_TEXT =
  "Blade could not find the send button in this compose window. Reload Gmail or switch the display language to English.";
const DRAFT_CHANGED_NOTICE_TEXT =
  "The draft changed after Blade checked it, so it was not sent. Blade is checking it again.";
const DRAFT_UNSTABLE_NOTICE_TEXT =
  "The draft kept changing while Blade was checking it, so nothing was sent. Wait a moment and send again.";
const DRAFT_UNREADABLE_NOTICE_TEXT =
  "Blade could not read this draft, so it was not sent. Reload Gmail and try again.";
const SCAN_FAILED_NOTICE_TEXT =
  "Blade could not finish checking this message, so nothing was sent. Try again.";
const SEND_CONTROL_MOVED_NOTICE_TEXT =
  "The send button moved. Click send again.";

function showComposeNotice(compose: Element, text: string): void {
  const noticeId = scopedSurfaceId(COMPOSE_NOTICE_SURFACE, compose);
  document.getElementById(noticeId)?.remove();
  const p = palette();

  const notice = styled(
    "div",
    `position:fixed;z-index:2147483645;box-sizing:border-box;display:flex;align-items:flex-start;gap:10px;max-width:420px;padding:10px 12px;background:${p.surface};color:${p.ink};border:1px solid ${p.rule};border-left:3px solid ${p.stop};border-radius:6px;font-family:${FONT_UI};font-size:12.5px;line-height:1.45;box-shadow:${p.shadow};`,
  );
  notice.id = noticeId;
  notice.setAttribute(BLADE_SURFACE_ATTRIBUTE, COMPOSE_NOTICE_SURFACE);
  notice.setAttribute("role", "alert");

  const message = styled("span", "flex:1;min-width:0;");
  message.textContent = text;

  const dismiss = styled(
    "button",
    `flex:none;height:26px;padding:0 10px;border-radius:4px;border:1px solid ${p.ruleStrong};background:${p.surface};color:${p.ink};font:inherit;font-size:12px;font-weight:500;cursor:pointer;`,
  );
  dismiss.type = "button";
  dismiss.textContent = "Dismiss";

  notice.append(message, dismiss);
  document.body.appendChild(notice);

  const rect = compose.getBoundingClientRect();
  notice.style.left = `${Math.max(8, rect.left + 8)}px`;
  notice.style.top = `${Math.max(8, rect.bottom - 96)}px`;

  const remove = (): void => notice.remove();
  dismiss.addEventListener("click", remove);
  setTimeout(remove, COMPOSE_NOTICE_MS);
}

function showSendControlNotice(compose: Element): void {
  showComposeNotice(compose, SEND_CONTROL_NOTICE_TEXT);
}

function startSendIntercept(compose: Element, trigger: SendTrigger): void {
  void handleSendIntercept(compose, trigger).catch((err: unknown) => {
    console.error("[Blade] send interception failed; nothing was sent", err);
    showComposeNotice(compose, scanFailureNoticeText(err));
  });
}

function scanFailureNoticeText(err: unknown): string {
  return err instanceof ComposeReadError
    ? DRAFT_UNREADABLE_NOTICE_TEXT
    : SCAN_FAILED_NOTICE_TEXT;
}

function instrumentCompose(compose: Element): void {
  if (instrumentedComposes.has(compose)) return;
  instrumentedComposes.add(compose);
  composeRegistry.add(compose);
  activeCompose ??= compose;

  compose.addEventListener(
    "focusin",
    () => {
      activeCompose = compose;
    },
    true,
  );
  compose.addEventListener(
    "pointerdown",
    () => {
      activeCompose = compose;
    },
    true,
  );
  compose.addEventListener(
    "click",
    () => {
      activeCompose = compose;
    },
    true,
  );

  compose.addEventListener(
    "change",
    (event) => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.type === "file") {
        captureFiles(compose, target.files);
      }
    },
    true,
  );

  compose.addEventListener(
    "drop",
    (event) => {
      const dt = (event as DragEvent).dataTransfer;
      if (dt?.files?.length) captureFiles(compose, dt.files);
    },
    true,
  );
}

type ComposeActivation =
  | { kind: "ignore" }
  | { kind: "no-send-control"; compose: Element }
  | { kind: "scan"; compose: Element; trigger: SendTrigger };

function resolveComposeActivation(target: Element | null): ComposeActivation {
  const labelled = findSendTrigger(target);
  if (labelled) {
    const compose = resolveComposeForTrigger(labelled.kind, labelled.element);
    return compose
      ? { kind: "scan", compose, trigger: labelled }
      : { kind: "ignore" };
  }

  const compose = findComposeAncestor(target);
  if (!compose) return { kind: "ignore" };

  const outcome = resolveComposeButtonActivation(compose, target);
  if (outcome.kind === "ignore") return { kind: "ignore" };
  if (outcome.kind === "no-send-control")
    return { kind: "no-send-control", compose };
  return { kind: "scan", compose, trigger: outcome.trigger };
}

function blockUntrustedCompose(compose: Element): void {
  console.warn("[Blade] no send control found in this compose; not sent");
  showSendControlNotice(compose);
}

function onDocumentClick(event: MouseEvent): void {
  if (bypassing) return;
  const target = event.target instanceof Element ? event.target : null;
  const activation = resolveComposeActivation(target);
  if (activation.kind === "ignore") return;
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
  if (activation.kind === "no-send-control") {
    blockUntrustedCompose(activation.compose);
    return;
  }
  startSendIntercept(activation.compose, activation.trigger);
}

const swallowedKeys = new WeakMap<Element, string>();

function onDocumentKeyDown(event: KeyboardEvent): void {
  if (bypassing) return;
  const target = event.target instanceof Element ? event.target : null;

  if (isSendShortcut(event)) {
    const compose = findComposeAncestor(target);
    if (!compose) return;
    const outcome = resolveSendShortcut(compose);
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    if (outcome.kind === "scan") {
      startSendIntercept(compose, outcome.trigger);
      return;
    }
    blockUntrustedCompose(compose);
    return;
  }

  if (!isActivationKey(event) || isTextEntryTarget(target)) return;
  const activation = resolveComposeActivation(target);
  if (activation.kind === "ignore") return;
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
  swallowedKeys.set(activation.compose, event.key);
  if (activation.kind === "no-send-control") {
    blockUntrustedCompose(activation.compose);
    return;
  }
  startSendIntercept(activation.compose, activation.trigger);
}

function onDocumentKeyUp(event: KeyboardEvent): void {
  const target = event.target instanceof Element ? event.target : null;
  const compose = findComposeAncestor(target);
  if (!compose) return;
  if (swallowedKeys.get(compose) !== event.key) return;
  swallowedKeys.delete(compose);
  event.stopPropagation();
  event.stopImmediatePropagation();
}

function composeForFileInput(input: HTMLInputElement): Element | null {
  const bound = fileInputCompose.get(input);
  if (bound?.isConnected && boundComposeStillOwns(bound, input, activeCompose))
    return bound;

  const resolved = resolveComposeFromContext(input);
  if (resolved) fileInputCompose.set(input, resolved);
  return resolved;
}

function onDocumentChange(event: Event): void {
  const path =
    typeof event.composedPath === "function" ? event.composedPath() : [];
  const candidateNodes = [event.target, ...path];
  const input = candidateNodes.find(
    (node): node is HTMLInputElement =>
      node instanceof HTMLInputElement && node.type === "file",
  );
  if (!input?.files?.length) return;

  const compose = composeForFileInput(input);
  if (!compose) {
    console.warn("[Blade] file picked with no compose to attribute it to");
    return;
  }
  captureFiles(compose, input.files);
}

const fileInputObserver = new MutationObserver((mutations) => {
  for (const m of mutations) {
    for (const node of m.addedNodes) {
      if (!(node instanceof HTMLElement)) continue;
      const inputs =
        node.tagName === "INPUT"
          ? [node as HTMLInputElement]
          : Array.from(
              node.querySelectorAll<HTMLInputElement>('input[type="file"]'),
            );
      for (const input of inputs) {
        if (input.type !== "file") continue;
        const owner = resolveComposeFromContext(input);
        if (owner) fileInputCompose.set(input, owner);
        input.addEventListener("change", () => {
          if (!input.files?.length) return;
          const compose = composeForFileInput(input);
          if (!compose) return;
          captureFiles(compose, input.files);
        });
      }
    }
  }
});

function onDocumentPaste(event: Event): void {
  const dt = (event as ClipboardEvent).clipboardData;
  if (!dt?.files?.length) return;
  const compose = resolveComposeFromContext(
    event.target instanceof Element ? event.target : null,
  );
  if (!compose) return;
  captureFiles(compose, dt.files);
}

function onDocumentDrop(event: Event): void {
  const dt = (event as DragEvent).dataTransfer;
  if (!dt?.files?.length) return;
  const compose = resolveComposeFromContext(
    event.target instanceof Element ? event.target : null,
  );
  if (!compose) return;
  captureFiles(compose, dt.files);
}

function observeComposeWindows(): void {
  const findAndInstrumentComposes = (): void => {
    document
      .querySelectorAll(
        'div[role="dialog"]:has(input[name="to"]), form:has(input[name="subjectbox"])',
      )
      .forEach((el) => instrumentCompose(el));
    document
      .querySelectorAll(
        'table:has(input[name="subjectbox"]):has([role="textbox"]), div:has(> input[name="to"]):has([role="textbox"])',
      )
      .forEach((el) => instrumentCompose(el));
  };

  findAndInstrumentComposes();

  let scheduled = false;
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      findAndInstrumentComposes();
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

function startGmailGuard(): void {
  chrome.storage.onChanged.addListener(onStoredStateChanged);
  void loadOrgState().then(() => maybeShowOrgBanner());

  document.addEventListener("click", onDocumentClick, true);
  document.addEventListener("keydown", onDocumentKeyDown, true);
  document.addEventListener("keyup", onDocumentKeyUp, true);
  document.addEventListener("change", onDocumentChange, true);
  document.addEventListener("paste", onDocumentPaste, true);
  document.addEventListener("drop", onDocumentDrop, true);

  fileInputObserver.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  observeComposeWindows();
}

if (claimInjection(GMAIL_GUARD_MARKER)) startGmailGuard();
