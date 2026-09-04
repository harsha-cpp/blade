import { BACKEND_URL } from "../config";
import { DEVICE_TOKEN_HEADER } from "../device-token";

export interface PublicDomain {
  domain: string;
  direction: string;
  classification: string;
}

interface PublicConfig {
  organization: { name: string; org_code: string };
  domains: PublicDomain[];
  blocked_domains?: PublicDomain[];
  fail_open?: boolean;
}

interface DeviceSelfConfig {
  device: { id: string; label: string };
  organization: { name: string };
  config: {
    fail_open?: boolean;
    domains: PublicDomain[];
    blocked_domains?: PublicDomain[];
  };
}

export interface ConfigCache {
  org_code: string;
  organization_name: string;
  domains: PublicDomain[];
  blocked_domains: PublicDomain[];
  fail_open: boolean;
  fetched_at: number;
  credential_key: string;
}

export type ConfigFetchResult =
  | { status: "ok"; config: ConfigCache }
  | { status: "not_found" }
  | { status: "unauthorized" }
  | { status: "transient_error" };

export function orgCodeCredentialKey(orgCode: string): string {
  return `org:${orgCode}`;
}

const DEVICE_KEY_HEX_CHARS = 16;

export async function deviceCredentialKey(token: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(token),
  );
  const hex = Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `device:${hex.slice(0, DEVICE_KEY_HEX_CHARS)}`;
}

export async function fetchPublicConfigByOrgCode(
  orgCode: string,
): Promise<ConfigFetchResult> {
  try {
    const url = `${BACKEND_URL}/api/v1/public/orgs/${encodeURIComponent(orgCode)}/config`;
    const res = await fetch(url, { method: "GET" });
    if (!res.ok) {
      console.warn("[Blade] Config fetch failed:", res.status);
      if (res.status === 404) return { status: "not_found" };
      return { status: "transient_error" };
    }
    const data = (await res.json()) as PublicConfig;
    return {
      status: "ok",
      config: {
        org_code: data.organization.org_code,
        organization_name: data.organization.name,
        domains: data.domains,
        blocked_domains: data.blocked_domains ?? [],
        fail_open: data.fail_open === true,
        fetched_at: Date.now(),
        credential_key: orgCodeCredentialKey(orgCode),
      },
    };
  } catch (err) {
    console.warn("[Blade] Config fetch error", err);
    return { status: "transient_error" };
  }
}

export async function fetchDeviceConfig(
  token: string,
  orgCode: string | null,
): Promise<ConfigFetchResult> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/devices/me`, {
      method: "GET",
      headers: { [DEVICE_TOKEN_HEADER]: token },
    });
    if (!res.ok) {
      console.warn("[Blade] Device config fetch failed:", res.status);
      if (res.status === 401 || res.status === 403) {
        return { status: "unauthorized" };
      }
      return { status: "transient_error" };
    }
    const data = (await res.json()) as DeviceSelfConfig;
    return {
      status: "ok",
      config: {
        org_code: orgCode ?? "",
        organization_name: data.organization.name,
        domains: data.config.domains,
        blocked_domains: data.config.blocked_domains ?? [],
        fail_open: data.config.fail_open === true,
        fetched_at: Date.now(),
        credential_key: await deviceCredentialKey(token),
      },
    };
  } catch (err) {
    console.warn("[Blade] Device config fetch error", err);
    return { status: "transient_error" };
  }
}
