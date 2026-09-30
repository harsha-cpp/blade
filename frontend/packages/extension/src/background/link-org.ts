import { isConfigFresh } from "../config-freshness";
import { normalizeOrgCode, type OrgLinkResult } from "../org-link";
import { DEVICE_TOKEN_STORAGE_KEY } from "../device-token";
import {
  fetchPublicConfigByOrgCode,
  orgCodeCredentialKey,
  type ConfigCache,
} from "./config-fetch";
import { serializeCredentialTask } from "./credential-tasks";

async function validateAndSave(raw: string): Promise<OrgLinkResult> {
  const code = normalizeOrgCode(raw);
  if (!code)
    return {
      ok: false,
      message:
        "Paste the organization code starting with BLD- from Settings, rather than the organization ID.",
    };
  const stored = await chrome.storage.local.get([
    "blade_org_code",
    "blade_config",
    DEVICE_TOKEN_STORAGE_KEY,
  ]);
  if (stored[DEVICE_TOKEN_STORAGE_KEY]) {
    return {
      ok: false,
      message:
        "This browser is enrolled with a device token. Clear it before linking a different organization.",
    };
  }
  const cached = stored.blade_config as ConfigCache | undefined;
  if (
    stored.blade_org_code === code &&
    cached?.credential_key === orgCodeCredentialKey(code) &&
    isConfigFresh(cached.fetched_at)
  ) {
    return {
      ok: true,
      orgCode: code,
      organizationName: cached.organization_name,
    };
  }
  const result = await fetchPublicConfigByOrgCode(code);
  if (result.status !== "ok") {
    return {
      ok: false,
      message:
        result.status === "not_found"
          ? "Organization code not found. Check the code with your administrator."
          : "Could not reach Blade to verify this code. Check your connection and try again.",
    };
  }
  // A token may have been saved while the network request was pending.
  const latest = await chrome.storage.local.get(DEVICE_TOKEN_STORAGE_KEY);
  if (latest[DEVICE_TOKEN_STORAGE_KEY]) {
    return {
      ok: false,
      message:
        "This browser was enrolled while linking. Reopen Blade to see its organization.",
    };
  }
  await chrome.storage.local.set({
    blade_org_code: code,
    blade_org_skipped: false,
    blade_config: result.config,
  });
  return {
    ok: true,
    orgCode: code,
    organizationName: result.config.organization_name,
  };
}

export function linkOrganization(code: string): Promise<OrgLinkResult> {
  // Popup and Gmail can request a link together; serialize commits and reuse a verified link.
  return serializeCredentialTask(() => validateAndSave(code)).catch(
    (): OrgLinkResult => ({
      ok: false,
      message: "Blade could not save the link. Please try again.",
    }),
  );
}
