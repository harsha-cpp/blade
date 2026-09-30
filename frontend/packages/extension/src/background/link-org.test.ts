import { beforeEach, describe, expect, it, vi } from "vitest";
import { linkOrganization } from "./link-org";
import { fetchPublicConfigByOrgCode, type ConfigCache } from "./config-fetch";

vi.mock("./config-fetch", async (original) => ({
  ...(await original<typeof import("./config-fetch")>()),
  fetchPublicConfigByOrgCode: vi.fn(),
}));
const config: ConfigCache = {
  org_code: "BLD-ABC123",
  organization_name: "Test Hospital",
  domains: [],
  blocked_domains: [],
  fail_open: false,
  fetched_at: Date.now(),
  credential_key: "org:BLD-ABC123",
};
let stored: Record<string, unknown>;
const set = vi.fn(async (values: Record<string, unknown>) => {
  Object.assign(stored, values);
});

beforeEach(() => {
  vi.clearAllMocks();
  stored = {};
  vi.stubGlobal("chrome", {
    storage: { local: { get: vi.fn(async () => ({ ...stored })), set } },
  });
  vi.mocked(fetchPublicConfigByOrgCode).mockResolvedValue({
    status: "ok",
    config,
  });
});

describe("verified organization linking", () => {
  it("normalizes and commits the code and verified policy together", async () => {
    expect(await linkOrganization(" bld-abc123 ")).toEqual({
      ok: true,
      orgCode: "BLD-ABC123",
      organizationName: "Test Hospital",
    });
    expect(set).toHaveBeenCalledWith({
      blade_org_code: "BLD-ABC123",
      blade_config: config,
      blade_org_skipped: false,
    });
  });
  it("rejects IDs, malformed codes, unknown codes and network failures without changing an existing link", async () => {
    stored = {
      blade_org_code: "BLD-OLD123",
      blade_config: { ...config, org_code: "BLD-OLD123" },
    };
    for (const code of ["some-org-id", "BLD-", "BLD-X/../../"])
      expect((await linkOrganization(code)).ok).toBe(false);
    for (const status of ["not_found", "transient_error"] as const) {
      vi.mocked(fetchPublicConfigByOrgCode).mockResolvedValue({ status });
      expect((await linkOrganization("BLD-ABC123")).ok).toBe(false);
    }
    expect(set).not.toHaveBeenCalled();
    expect(stored.blade_org_code).toBe("BLD-OLD123");
  });
  it("deduplicates concurrent linking requests and needs no repeated save", async () => {
    const results = await Promise.all([
      linkOrganization("BLD-ABC123"),
      linkOrganization("BLD-ABC123"),
    ]);
    expect(results.every((result) => result.ok)).toBe(true);
    expect(fetchPublicConfigByOrgCode).toHaveBeenCalledTimes(1);
    expect(set).toHaveBeenCalledTimes(1);
  });
  it("does not replace an enrolled device's organization", async () => {
    stored.blade_device_token = "existing-token";
    expect((await linkOrganization("BLD-ABC123")).ok).toBe(false);
    expect(fetchPublicConfigByOrgCode).not.toHaveBeenCalled();
  });
});
