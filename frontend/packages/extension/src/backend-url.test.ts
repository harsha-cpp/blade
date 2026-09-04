import { describe, expect, it } from "vitest";
import {
  DEV_BACKEND_URL,
  PROD_BACKEND_URL,
  resolveBackendTarget,
} from "./backend-url";

describe("backend target resolution", () => {
  it("defaults to localhost in development", () => {
    expect(resolveBackendTarget(undefined, true).url).toBe(DEV_BACKEND_URL);
    expect(resolveBackendTarget("", true).url).toBe(DEV_BACKEND_URL);
  });

  it("names the launch backend as the production host", () => {
    expect(PROD_BACKEND_URL).toBe("https://api.tryblade.in");
  });

  it("refuses to build for production without an explicit backend", () => {
    expect(() => resolveBackendTarget(undefined, false)).toThrow(
      /must be set for a production build/,
    );
    expect(() => resolveBackendTarget("", false)).toThrow(
      /must be set for a production build/,
    );
    expect(() => resolveBackendTarget("   ", false)).toThrow(
      /must be set for a production build/,
    );
  });

  it("derives the manifest host permission from the same value as the runtime URL", () => {
    const target = resolveBackendTarget("https://api.hospital.example", false);
    expect(target.url).toBe("https://api.hospital.example");
    expect(target.hostPermission).toBe("https://api.hospital.example/*");
  });

  it("trims a trailing slash but keeps a base path", () => {
    expect(
      resolveBackendTarget("https://api.hospital.example/", false).url,
    ).toBe("https://api.hospital.example");
    const based = resolveBackendTarget(
      "https://gw.hospital.example/blade/",
      false,
    );
    expect(based.url).toBe("https://gw.hospital.example/blade");
    expect(based.hostPermission).toBe("https://gw.hospital.example/*");
  });

  it("allows plain http only for a loopback development backend", () => {
    expect(resolveBackendTarget("http://localhost:8000", true).url).toBe(
      "http://localhost:8000",
    );
    expect(resolveBackendTarget("http://127.0.0.1:9000", false).url).toBe(
      "http://127.0.0.1:9000",
    );
    expect(() =>
      resolveBackendTarget("http://api.hospital.example", false),
    ).toThrow(/https/);
  });

  it("fails the build on a malformed value instead of silently using the default", () => {
    expect(() => resolveBackendTarget("api.hospital.example", false)).toThrow(
      /not a valid URL/,
    );
    expect(() =>
      resolveBackendTarget("ftp://api.hospital.example", false),
    ).toThrow(/http/);
  });
});
