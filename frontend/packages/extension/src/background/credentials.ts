import { checkDeviceToken, DEVICE_TOKEN_STORAGE_KEY } from "../device-token";

export const ORG_CODE_STORAGE_KEY = "blade_org_code";

export type ScanCredential =
  | { kind: "device"; token: string; orgCode: string | null }
  | { kind: "org_code"; orgCode: string };

export async function storedOrgCode(): Promise<string | null> {
  const stored = await chrome.storage.local.get(ORG_CODE_STORAGE_KEY);
  const code = (stored[ORG_CODE_STORAGE_KEY] as string | undefined)
    ?.trim()
    .toUpperCase();
  return code ? code : null;
}

export async function storedDeviceToken(): Promise<string | null> {
  const stored = await chrome.storage.local.get(DEVICE_TOKEN_STORAGE_KEY);
  const raw = stored[DEVICE_TOKEN_STORAGE_KEY] as string | undefined;
  if (!raw) return null;
  const checked = checkDeviceToken(raw);
  return checked.ok ? checked.token : null;
}

export async function resolveScanCredential(): Promise<ScanCredential | null> {
  const [token, orgCode] = await Promise.all([
    storedDeviceToken(),
    storedOrgCode(),
  ]);
  if (token) return { kind: "device", token, orgCode };
  if (orgCode) return { kind: "org_code", orgCode };
  return null;
}

export function isCredentialStorageChange(changes: {
  [key: string]: chrome.storage.StorageChange;
}): boolean {
  return (
    changes[ORG_CODE_STORAGE_KEY] !== undefined ||
    changes[DEVICE_TOKEN_STORAGE_KEY] !== undefined
  );
}
