import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveScanCredential } from "./credentials";

const VALID =
  "bladedev_0193f2c4-8e4a-7f11-9c33-4a2b1d6e5f70.qkR3s7Xm2wPzT8vLbN4hGdJyQ1eF6uCa";

function stubStorage(local: Record<string, unknown>): void {
  vi.stubGlobal("chrome", {
    storage: {
      local: {
        get: vi.fn(async (keys: string | string[]) => {
          const wanted = typeof keys === "string" ? [keys] : keys;
          const out: Record<string, unknown> = {};
          for (const key of wanted) {
            if (key in local) out[key] = local[key];
          }
          return out;
        }),
      },
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("credential precedence", () => {
  it("prefers a stored device token over the org code", async () => {
    stubStorage({ blade_device_token: VALID, blade_org_code: "bld-abc123" });
    expect(await resolveScanCredential()).toEqual({
      kind: "device",
      token: VALID,
      orgCode: "BLD-ABC123",
    });
  });

  it("falls back to the org code when no token is stored", async () => {
    stubStorage({ blade_org_code: "bld-abc123" });
    expect(await resolveScanCredential()).toEqual({
      kind: "org_code",
      orgCode: "BLD-ABC123",
    });
  });

  it("ignores a stored value that is not a device token", async () => {
    stubStorage({ blade_device_token: "nonsense", blade_org_code: "BLD-1234" });
    expect(await resolveScanCredential()).toEqual({
      kind: "org_code",
      orgCode: "BLD-1234",
    });
  });

  it("reports no credential when the install is untouched", async () => {
    stubStorage({});
    expect(await resolveScanCredential()).toBeNull();
  });

  it("carries a device token with no org code alongside it", async () => {
    stubStorage({ blade_device_token: VALID });
    expect(await resolveScanCredential()).toEqual({
      kind: "device",
      token: VALID,
      orgCode: null,
    });
  });
});
