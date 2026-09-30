import {
  authOutcomeForStatus,
  recordAuthOutcome,
  type AuthOutcome,
} from "../auth-outcome";
import { WEB_BLOCK_TIMEOUT_MS } from "../config";
import { parseBackendRequest } from "../messages";
import { handleBackendRequest, prepareWebBlockRequest } from "./backend-bridge";
import {
  isCredentialStorageChange,
  resolveScanCredential,
} from "./credentials";
import { injectionSummaryLine, injectIntoOpenTabs } from "./inject";
import { createWebBlockGate } from "./web-block-gate";
import {
  deviceCredentialKey,
  fetchDeviceConfig,
  fetchPublicConfigByOrgCode,
  orgCodeCredentialKey,
  type ConfigCache,
} from "./config-fetch";
import { WEB_BLOCK_MESSAGE, type WebBlockReport } from "../web-block";
import { LINK_ORG_MESSAGE } from "../org-link";
import { linkOrganization } from "./link-org";
import { isConfigFresh } from "../config-freshness";
import { serializeCredentialTask } from "./credential-tasks";
import { linkDevice } from "./link-device";

const REFRESH_ALARM = "blade-config-refresh";

const webBlockGate = createWebBlockGate();

async function reportWebBlock(report: WebBlockReport): Promise<void> {
  const credential = await resolveScanCredential();
  if (!credential) return;

  const stored = await chrome.storage.local.get("blade_last_user_email");
  const userEmail =
    (stored.blade_last_user_email as string | undefined) ?? null;
  const { url, init } = prepareWebBlockRequest(
    report,
    credential,
    crypto.randomUUID(),
    userEmail,
  );
  const byDevice = credential.kind === "device";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), WEB_BLOCK_TIMEOUT_MS);
  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    if (byDevice) {
      await recordAuthOutcome(authOutcomeForStatus(res.status, Date.now()));
    }
    if (!res.ok) console.warn("[Blade] web block report rejected:", res.status);
  } catch (err) {
    if (byDevice) {
      const offline: AuthOutcome = { kind: "network", at: Date.now() };
      await recordAuthOutcome(offline);
    }
    console.warn("[Blade] web block report failed", err);
  } finally {
    clearTimeout(timer);
  }
}

let refreshGeneration = 0;

function refresh(): Promise<void> {
  return serializeCredentialTask(refreshNow);
}

async function refreshNow(): Promise<void> {
  const generation = ++refreshGeneration;
  const credential = await resolveScanCredential();
  if (!credential) {
    await chrome.storage.local.remove("blade_config");
    return;
  }

  const expectedKey =
    credential.kind === "device"
      ? await deviceCredentialKey(credential.token)
      : orgCodeCredentialKey(credential.orgCode);
  const stored = await chrome.storage.local.get("blade_config");
  const cached = stored.blade_config as ConfigCache | undefined;
  if (cached?.credential_key !== expectedKey) {
    await chrome.storage.local.remove("blade_config");
  }

  const configResult =
    credential.kind === "device"
      ? await fetchDeviceConfig(credential.token, credential.orgCode)
      : await fetchPublicConfigByOrgCode(credential.orgCode);

  const latest = await resolveScanCredential();
  if (
    generation !== refreshGeneration ||
    JSON.stringify(latest) !== JSON.stringify(credential)
  )
    return;

  if (configResult.status === "ok") {
    await chrome.storage.local.set({ blade_config: configResult.config });
    if (credential.kind === "device") {
      await recordAuthOutcome({ kind: "ok", at: Date.now() });
    }
    return;
  }
  if (configResult.status === "unauthorized") {
    await chrome.storage.local.remove("blade_config");
    await recordAuthOutcome({ kind: "unauthorized", at: Date.now() });
    return;
  }
  if (configResult.status === "not_found") {
    await chrome.storage.local.remove("blade_config");
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(REFRESH_ALARM, {
    periodInMinutes: 5,
    delayInMinutes: 0.1,
  });
  void refresh();
  void injectIntoOpenTabs()
    .then((summary) => {
      console.info(injectionSummaryLine(summary));
    })
    .catch((err: unknown) => {
      console.warn("[Blade] could not reach every open tab after install", err);
    });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(REFRESH_ALARM, {
    periodInMinutes: 5,
    delayInMinutes: 0.1,
  });
  void refresh();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === REFRESH_ALARM) void refresh();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  if (isCredentialStorageChange(changes)) {
    // Linking commits a verified config together with its credential.
    const config = changes.blade_config?.newValue as ConfigCache | undefined;
    if (!config || !isConfigFresh(config.fetched_at)) void refresh();
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id) return false;
  if (message?.type === "LINK_DEVICE" && typeof message.token === "string") {
    if (sender.url !== chrome.runtime.getURL("src/popup/index.html"))
      return false;
    void linkDevice(message.token).then(sendResponse);
    return true;
  }
  if (message?.type === LINK_ORG_MESSAGE && typeof message.code === "string") {
    void linkOrganization(message.code).then(sendResponse);
    return true;
  }
  if (message?.type === "REFRESH_CONFIG") {
    void refresh()
      .then(() => sendResponse({ ok: true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (message?.type === WEB_BLOCK_MESSAGE) {
    const report = webBlockGate.admit(message, sender);
    const done = report ? reportWebBlock(report) : Promise.resolve();
    void done.then(() => sendResponse({ ok: true }));
    return true;
  }
  const backendRequest = parseBackendRequest(message);
  if (backendRequest) {
    void handleBackendRequest(backendRequest).then(sendResponse);
    return true;
  }
  return false;
});
