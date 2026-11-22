import { StrictMode, useEffect, useState, useCallback, useRef } from "react";
import { isConfigFresh } from "../config-freshness";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { TabProtection } from "./TabProtection";
import { requestOrgLink, normalizeOrgCode } from "../org-link";
import type { DeviceLinkResult } from "../background/link-device";
import {
  LAST_AUTH_OUTCOME_STORAGE_KEY,
  parseAuthOutcome,
  type AuthOutcome,
} from "../auth-outcome";
import {
  checkDeviceToken,
  DEVICE_TOKEN_STORAGE_KEY,
  maskDeviceToken,
} from "../device-token";
import {
  deviceTokenStatus,
  migrationHint,
  orgCodeHint,
  protectionStatus,
} from "./status";

interface CachedConfig {
  org_code: string;
  organization_name: string;
  fetched_at: number;
}

function Popup() {
  const [orgCode, setOrgCodeInput] = useState("");
  const [savedCode, setSavedCode] = useState<string | null>(null);
  const [orgName, setOrgName] = useState<string | null>(null);
  const [hasConfig, setHasConfig] = useState(false);
  const [showSaved, setShowSaved] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [savedToken, setSavedToken] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [showTokenSaved, setShowTokenSaved] = useState(false);
  const [lastAuth, setLastAuth] = useState<AuthOutcome | null>(null);
  const [orgError, setOrgError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);

  useEffect(() => {
    chrome.storage.local.get(
      ["blade_org_code", "blade_config", DEVICE_TOKEN_STORAGE_KEY],
      (result) => {
        const code = (result.blade_org_code as string | undefined) ?? "";
        setOrgCodeInput(code);
        setSavedCode(code || null);
        const config = result.blade_config as CachedConfig | undefined;
        setOrgName(config?.organization_name ?? null);
        setHasConfig(isConfigFresh(config?.fetched_at));
        setSavedToken(
          (result[DEVICE_TOKEN_STORAGE_KEY] as string | undefined) ?? null,
        );
      },
    );
    chrome.storage.session.get(LAST_AUTH_OUTCOME_STORAGE_KEY, (result) => {
      setLastAuth(parseAuthOutcome(result[LAST_AUTH_OUTCOME_STORAGE_KEY]));
    });
    const listener = (
      changes: { [k: string]: chrome.storage.StorageChange },
      area: chrome.storage.AreaName,
    ): void => {
      if (area === "session" && changes[LAST_AUTH_OUTCOME_STORAGE_KEY]) {
        setLastAuth(
          parseAuthOutcome(changes[LAST_AUTH_OUTCOME_STORAGE_KEY]?.newValue),
        );
        return;
      }
      if (area !== "local") return;
      if (changes.blade_config) {
        const cfg = changes.blade_config.newValue as CachedConfig | undefined;
        setOrgName(cfg?.organization_name ?? null);
        setHasConfig(isConfigFresh(cfg?.fetched_at));
      }
      if (changes.blade_org_code) {
        setSavedCode(
          (changes.blade_org_code.newValue as string | undefined) ?? null,
        );
      }
      if (changes[DEVICE_TOKEN_STORAGE_KEY]) {
        setSavedToken(
          (changes[DEVICE_TOKEN_STORAGE_KEY]?.newValue as string | undefined) ??
            null,
        );
      }
    };
    chrome.storage.onChanged.addListener(listener);
    return () => chrome.storage.onChanged.removeListener(listener);
  }, []);

  const handleSave = useCallback(async () => {
    const trimmed = orgCode.trim().toUpperCase();
    if (savingRef.current || (trimmed === savedCode && hasConfig)) return;
    savingRef.current = true;
    setSaving(true);
    setOrgError(null);
    setShowSaved(false);
    try {
      const result = await requestOrgLink(trimmed);
      if (!result.ok) {
        setOrgError(result.message);
        return;
      }
      setOrgCodeInput(result.orgCode);
      setSavedCode(result.orgCode);
      setOrgName(result.organizationName);
      setHasConfig(true);
      setShowSaved(true);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [orgCode, savedCode, hasConfig]);

  const handleSaveToken = useCallback(async () => {
    if (savingRef.current) return;
    const checked = checkDeviceToken(tokenInput);
    if (!checked.ok) {
      setTokenError(checked.message);
      return;
    }
    savingRef.current = true;
    setSaving(true);
    setTokenError(null);
    setShowTokenSaved(false);
    try {
      const result: DeviceLinkResult = await chrome.runtime.sendMessage({
        type: "LINK_DEVICE",
        token: checked.token,
      });
      if (!result.ok) {
        setTokenError(result.message);
        return;
      }
      setOrgName(result.organizationName);
      setHasConfig(true);
      setSavedCode(null);
      setOrgCodeInput("");
      setSavedToken(checked.token);
      setLastAuth(null);
      setTokenInput("");
      setShowTokenSaved(true);
    } catch {
      setTokenError("Blade could not save the device token. Please try again.");
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [tokenInput]);

  const handleClearToken = useCallback(async () => {
    await chrome.storage.local.remove([
      DEVICE_TOKEN_STORAGE_KEY,
      "blade_config",
    ]);
    await chrome.storage.session.remove(LAST_AUTH_OUTCOME_STORAGE_KEY);
    setHasConfig(false);
    setSavedToken(null);
    setLastAuth(null);
    setTokenInput("");
    setTokenError(null);
    setShowTokenSaved(false);
  }, []);

  const hasChanges =
    (orgCode.trim().toUpperCase() !== (savedCode ?? "") || !hasConfig) &&
    normalizeOrgCode(orgCode) !== null;
  const statusInput = {
    orgCode: savedCode,
    hasConfig,
    orgName,
    deviceToken: savedToken,
    lastAuth,
  };
  const status = protectionStatus(statusInput);
  const tokenStatus = deviceTokenStatus(statusInput);
  const nudge = migrationHint(statusInput);

  return (
    <div className="popup">
      <div className="popup-header">
        <div className="popup-brand">
          <span className="popup-title">Blade</span>
        </div>
        <span className="popup-version">
          v{chrome.runtime.getManifest().version}
        </span>
      </div>

      <div className="popup-body">
        <TabProtection />
        <div
          className={`status-card${
            status.state === "protected" ? "" : " is-unlinked"
          }`}
          role="status"
        >
          <span className="status-dot" aria-hidden="true" />
          <div>
            <div className="status-label">{status.label}</div>
            <div className="status-desc">{status.description}</div>
          </div>
        </div>

        <div>
          <div className="settings-title">Organization</div>
          <div className="settings-field">
            <label className="settings-label" htmlFor="org-code">
              Organization code
            </label>
            <div className="settings-input-row">
              <input
                id="org-code"
                className="settings-input"
                type="text"
                placeholder="BLD-XXXXXX"
                autoComplete="off"
                spellCheck={false}
                value={orgCode}
                onChange={(e) => {
                  setOrgCodeInput(e.target.value);
                  setOrgError(null);
                  setShowSaved(false);
                }}
                onKeyDown={(e) => e.key === "Enter" && handleSave()}
              />
              <button
                type="button"
                className="settings-btn"
                onClick={handleSave}
                disabled={!hasChanges || saving || Boolean(savedToken)}
              >
                {saving && !tokenInput ? "Checking…" : "Link"}
              </button>
            </div>
            {orgError && (
              <div className="settings-error" role="alert">
                {orgError}
              </div>
            )}
            {showSaved && (
              <div className="settings-saved" role="status">
                Linked. Saved for this Chrome profile.
              </div>
            )}
            {orgName && savedCode && !showSaved && (
              <div className="settings-meta">
                Linked to <span className="settings-meta-org">{orgName}</span>
              </div>
            )}
            <p className="settings-hint">{orgCodeHint(statusInput)}</p>
          </div>
        </div>

        <div>
          <div className="settings-title">Device</div>
          <div className="settings-field">
            <label className="settings-label" htmlFor="device-token">
              Device token
            </label>
            <div className="settings-input-row">
              <input
                id="device-token"
                className="settings-input is-secret"
                type="password"
                placeholder="bladedev_..."
                autoComplete="off"
                spellCheck={false}
                value={tokenInput}
                onChange={(e) => {
                  setTokenInput(e.target.value);
                  setTokenError(null);
                }}
                onKeyDown={(e) => e.key === "Enter" && handleSaveToken()}
              />
              <button
                type="button"
                className="settings-btn"
                onClick={handleSaveToken}
                disabled={tokenInput.trim().length === 0 || saving}
              >
                Save
              </button>
              <button
                type="button"
                className="settings-btn is-quiet"
                onClick={handleClearToken}
                disabled={!savedToken || saving}
              >
                Clear
              </button>
            </div>
            {tokenError && <div className="settings-error">{tokenError}</div>}
            {showTokenSaved && <div className="settings-saved">Enrolled.</div>}
            {savedToken && !showTokenSaved && (
              <div className="settings-meta">
                <span className="settings-meta-token">
                  {maskDeviceToken(savedToken)}
                </span>
              </div>
            )}
            <div
              className={`settings-meta${
                tokenStatus.state === "rejected" ? " is-rejected" : ""
              }`}
            >
              {tokenStatus.message}
            </div>
            <p className="settings-hint">
              Paste the device token from the dashboard Devices page. It
              identifies this install on its own, so it can be revoked without
              re-keying everyone.
            </p>
            {nudge && <p className="settings-hint is-nudge">{nudge}</p>}
          </div>
        </div>
      </div>

      <div className="popup-footer">Blade Healthcare DLP</div>
    </div>
  );
}

const root = document.getElementById("root");
if (root)
  createRoot(root).render(
    <StrictMode>
      <Popup />
    </StrictMode>,
  );
