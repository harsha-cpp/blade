import { beforeEach, expect, it, vi } from "vitest";
import { linkDevice } from "./link-device";
import { fetchDeviceConfig, type ConfigCache } from "./config-fetch";

vi.mock("./config-fetch", async (original) => ({
  ...(await original<typeof import("./config-fetch")>()),
  fetchDeviceConfig: vi.fn(),
}));
const token = "bladedev_0192abcd.secretsecretsecretsecret";
const config: ConfigCache = {
  org_code: "",
  organization_name: "Test Hospital",
  domains: [],
  blocked_domains: [],
  fail_open: false,
  fetched_at: Date.now(),
  credential_key: "device:hash",
};
const set = vi.fn();
const remove = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("chrome", { storage: { local: { set }, session: { remove } } });
});

it("verifies the device and saves its organization and credential together", async () => {
  vi.mocked(fetchDeviceConfig).mockResolvedValue({ status: "ok", config });
  expect(await linkDevice(token)).toEqual({
    ok: true,
    organizationName: "Test Hospital",
  });
  expect(set).toHaveBeenCalledWith({
    blade_device_token: token,
    blade_config: config,
    blade_org_code: "",
    blade_org_skipped: false,
  });
  expect(remove).toHaveBeenCalledTimes(1);
});

it("leaves the previous link intact when a token is invalid or verification fails", async () => {
  expect((await linkDevice("invalid-token")).ok).toBe(false);
  expect(fetchDeviceConfig).not.toHaveBeenCalled();
  for (const status of ["unauthorized", "transient_error"] as const) {
    vi.mocked(fetchDeviceConfig).mockResolvedValue({ status });
    expect((await linkDevice(token)).ok).toBe(false);
  }
  expect(set).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
});
