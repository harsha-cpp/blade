import { StrictMode, useEffect, useState, useCallback } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
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

  useEffect(() => {
    chrome.storage.local.get(
      ["blade_org_code", "blade_config", DEVICE_TOKEN_STORAGE_KEY],
      (result) => {
        const code = (result.blade_org_code as string | undefined) ?? "";
        setOrgCodeInput(code);
        setSavedCode(code || null);
        const config = result.blade_config as CachedConfig | undefined;
        setOrgName(config?.organization_name ?? null);
        setHasConfig(Boolean(config));
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
        setHasConfig(Boolean(cfg));
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

  const handleSave = useCallback(() => {
    const trimmed = orgCode.trim().toUpperCase();
    if (trimmed.length < 4 || trimmed === savedCode) return;
    chrome.storage.local.set({
      blade_org_code: trimmed,
      blade_org_skipped: false,
    });
    setSavedCode(trimmed);
    setShowSaved(true);
    setTimeout(() => setShowSaved(false), 2000);
    chrome.runtime.sendMessage({ type: "REFRESH_CONFIG" }).catch(() => {});
  }, [orgCode, savedCode]);

  const handleSaveToken = useCallback(() => {
    const checked = checkDeviceToken(tokenInput);
    if (!checked.ok) {
      setTokenError(checked.message);
      return;
    }
    setTokenError(null);
    chrome.storage.local.set({ [DEVICE_TOKEN_STORAGE_KEY]: checked.token });
    chrome.storage.session.remove(LAST_AUTH_OUTCOME_STORAGE_KEY);
    setSavedToken(checked.token);
    setLastAuth(null);
    setTokenInput("");
    setShowTokenSaved(true);
    setTimeout(() => setShowTokenSaved(false), 2000);
  }, [tokenInput]);

  const handleClearToken = useCallback(() => {
    chrome.storage.local.remove(DEVICE_TOKEN_STORAGE_KEY);
    chrome.storage.session.remove(LAST_AUTH_OUTCOME_STORAGE_KEY);
    setSavedToken(null);
    setLastAuth(null);
    setTokenInput("");
    setTokenError(null);
    setShowTokenSaved(false);
  }, []);

  const hasChanges =
    orgCode.trim().toUpperCase() !== (savedCode ?? "") &&
    orgCode.trim().length >= 4;
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
                onChange={(e) => setOrgCodeInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSave()}
              />
              <button
                type="button"
                className="settings-btn"
                onClick={handleSave}
                disabled={!hasChanges}
              >
                Link
              </button>
            </div>
            {showSaved && <div className="settings-saved">Linked.</div>}
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
                disabled={tokenInput.trim().length === 0}
              >
                Save
              </button>
              <button
                type="button"
                className="settings-btn is-quiet"
                onClick={handleClearToken}
                disabled={!savedToken}
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
