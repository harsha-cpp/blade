import { afterEach, describe, expect, it, vi } from "vitest";
import { BACKEND_URL } from "../config";
import { DEVICE_TOKEN_HEADER } from "../device-token";
import {
  deviceCredentialKey,
  fetchDeviceConfig,
  fetchPublicConfigByOrgCode,
  orgCodeCredentialKey,
} from "./config-fetch";

const TOKEN = "bladedev_0192abcd.secretsecretsecretsecret";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function lastCall(mock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  const call = mock.mock.calls[0];
  if (!call) throw new Error("fetch was not called");
  return call as unknown as [string, RequestInit];
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("fetchDeviceConfig", () => {
  it("sends the device token header and maps the self config", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, {
        device: { id: "d1", label: "Ward tablet" },
        organization: { name: "Shape Hospital" },
        config: {
          fail_open: false,
          domains: [
            {
              domain: "shapehospital.in",
              direction: "both",
              classification: "internal",
            },
          ],
          blocked_domains: [],
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchDeviceConfig(TOKEN, null);

    const [url, init] = lastCall(fetchMock);
    expect(url).toBe(`${BACKEND_URL}/api/v1/devices/me`);
    expect((init.headers as Record<string, string>)[DEVICE_TOKEN_HEADER]).toBe(
      TOKEN,
    );
    expect(url).not.toContain("org_code");
    expect(result.status).toBe("ok");
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.config.organization_name).toBe("Shape Hospital");
    expect(result.config.org_code).toBe("");
    expect(result.config.domains[0]?.domain).toBe("shapehospital.in");
    expect(result.config.fail_open).toBe(false);
    expect(result.config.credential_key).toBe(await deviceCredentialKey(TOKEN));
  });

  it("keys the cache on a hash that cannot be turned back into the token", async () => {
    const key = await deviceCredentialKey(TOKEN);

    expect(key.startsWith("device:")).toBe(true);
    expect(key).not.toContain(TOKEN);
    for (let start = 0; start + 6 <= TOKEN.length; start += 1) {
      expect(key).not.toContain(TOKEN.slice(start, start + 6));
    }
    expect(await deviceCredentialKey(TOKEN)).toBe(key);
    expect(await deviceCredentialKey(`${TOKEN}x`)).not.toBe(key);
  });

  it("keeps a stored org code alongside a device-fetched config", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(200, {
          device: { id: "d1", label: "x" },
          organization: { name: "Shape Hospital" },
          config: { domains: [] },
        }),
      ),
    );

    const result = await fetchDeviceConfig(TOKEN, "BLD-ABC123");

    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.config.org_code).toBe("BLD-ABC123");
    expect(result.config.blocked_domains).toEqual([]);
  });

  it("reports a rejected token as unauthorized so the config is dropped", async () => {
    for (const status of [401, 403]) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => jsonResponse(status, { detail: "no" })),
      );
      const result = await fetchDeviceConfig(TOKEN, null);
      expect(result.status).toBe("unauthorized");
    }
  });

  it("treats a server error or network failure as transient", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(503, {})),
    );
    expect((await fetchDeviceConfig(TOKEN, null)).status).toBe(
      "transient_error",
    );

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("offline");
      }),
    );
    expect((await fetchDeviceConfig(TOKEN, null)).status).toBe(
      "transient_error",
    );
  });
});

describe("fetchPublicConfigByOrgCode", () => {
  it("does not accept a mismatched organization or malformed policy", async () => {
    for (const body of [
      {
        organization: { name: "Other Hospital", org_code: "BLD-OTHER1" },
        domains: [],
      },
      { organization: { name: "Test Hospital", org_code: "BLD-ABC123" } },
    ]) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => jsonResponse(200, body)),
      );
      expect((await fetchPublicConfigByOrgCode("BLD-ABC123")).status).toBe(
        "transient_error",
      );
    }
  });
  it("still resolves by org code with its own cache key", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, {
        organization: { name: "Shape Hospital", org_code: "BLD-ABC123" },
        domains: [],
        blocked_domains: [],
        fail_open: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchPublicConfigByOrgCode("BLD-ABC123");

    const [url] = lastCall(fetchMock);
    expect(url).toBe(`${BACKEND_URL}/api/v1/public/orgs/BLD-ABC123/config`);
    if (result.status !== "ok") throw new Error("expected ok");
    expect(result.config.org_code).toBe("BLD-ABC123");
    expect(result.config.fail_open).toBe(true);
    expect(result.config.credential_key).toBe(
      orgCodeCredentialKey("BLD-ABC123"),
    );
  });

  it("maps an unknown org code to not_found", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(404, {})),
    );
    expect((await fetchPublicConfigByOrgCode("BLD-NOPE00")).status).toBe(
      "not_found",
    );
  });
});
