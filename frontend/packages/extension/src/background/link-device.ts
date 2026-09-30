import { checkDeviceToken, DEVICE_TOKEN_STORAGE_KEY } from "../device-token";
import { LAST_AUTH_OUTCOME_STORAGE_KEY } from "../auth-outcome";
import { fetchDeviceConfig } from "./config-fetch";
import { serializeCredentialTask } from "./credential-tasks";

export type DeviceLinkResult =
  | { ok: true; organizationName: string }
  | { ok: false; message: string };

export function linkDevice(raw: string): Promise<DeviceLinkResult> {
  return serializeCredentialTask(async (): Promise<DeviceLinkResult> => {
    const checked = checkDeviceToken(raw);
    if (!checked.ok) return checked;
    const result = await fetchDeviceConfig(checked.token, null);
    if (result.status !== "ok")
      return {
        ok: false,
        message:
          result.status === "unauthorized"
            ? "This device token was rejected. Ask your administrator for a new token."
            : "Could not verify the device token. Check your connection and try again.",
      };
    await chrome.storage.local.set({
      [DEVICE_TOKEN_STORAGE_KEY]: checked.token,
      blade_config: result.config,
      blade_org_code: "",
      blade_org_skipped: false,
    });
    await chrome.storage.session.remove(LAST_AUTH_OUTCOME_STORAGE_KEY);
    return { ok: true, organizationName: result.config.organization_name };
  }).catch(() => ({
    ok: false,
    message: "Blade could not save the device token. Please try again.",
  }));
}
