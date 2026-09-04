import { describe, expect, it } from "vitest";
import { CONFIG_MAX_AGE_MS, isConfigFresh } from "./config-freshness";

const NOW = 1_800_000_000_000;

describe("how long a cached org config counts", () => {
  it("accepts a config fetched moments ago", () => {
    expect(isConfigFresh(NOW - 1_000, NOW)).toBe(true);
  });

  it("accepts a config fetched just inside a day", () => {
    expect(isConfigFresh(NOW - CONFIG_MAX_AGE_MS, NOW)).toBe(true);
  });

  it("treats a config older than a day as absent", () => {
    expect(isConfigFresh(NOW - CONFIG_MAX_AGE_MS - 1, NOW)).toBe(false);
  });

  it("treats a config with no fetch time as absent", () => {
    expect(isConfigFresh(undefined, NOW)).toBe(false);
    expect(isConfigFresh(Number.NaN, NOW)).toBe(false);
  });

  it("holds a day in milliseconds", () => {
    expect(CONFIG_MAX_AGE_MS).toBe(86_400_000);
  });
});
