import { describe, expect, it } from "vitest";
import type { AuthOutcome } from "../auth-outcome";
import {
  deviceTokenStatus,
  migrationHint,
  orgCodeHint,
  protectionStatus,
  type ProtectionInput,
} from "./status";

const TOKEN = "bladedev_0193f2c4.qkR3s7Xm";

function input(overrides: Partial<ProtectionInput> = {}): ProtectionInput {
  return {
    orgCode: null,
    hasConfig: false,
    orgName: null,
    deviceToken: null,
    lastAuth: null,
    ...overrides,
  };
}

function outcome(kind: AuthOutcome["kind"]): AuthOutcome {
  return { kind, at: 1_700_000_000_000 };
}

describe("popup protection status", () => {
  it("reports an install that was never linked", () => {
    const status = protectionStatus(input());

    expect(status.state).toBe("unlinked");
    expect(status.label).toBe("Not linked");
  });

  it("separates a mistyped code from a working install", () => {
    const status = protectionStatus(input({ orgCode: "BLD-NOPE" }));

    expect(status.state).toBe("unconfigured");
    expect(status.label).toBe("Configuration unavailable");
    expect(status.label).not.toBe("Protection on");
    expect(status.description).toMatch(/held for review/i);
  });

  it("reports protection only once a policy is cached", () => {
    const status = protectionStatus(
      input({
        orgCode: "BLD-123456",
        hasConfig: true,
        orgName: "Sunrise Hospital",
      }),
    );

    expect(status.state).toBe("protected");
    expect(status.label).toBe("Protection on");
    expect(status.description).toContain("Sunrise Hospital");
  });

  it("still reports protection when the organization name is missing", () => {
    const status = protectionStatus(
      input({ orgCode: "BLD-123456", hasConfig: true }),
    );

    expect(status.state).toBe("protected");
    expect(status.description).toMatch(/patient data/i);
  });

  it("stops claiming protection once the device token is rejected", () => {
    const status = protectionStatus(
      input({
        orgCode: "BLD-123456",
        hasConfig: true,
        orgName: "Sunrise Hospital",
        deviceToken: TOKEN,
        lastAuth: outcome("unauthorized"),
      }),
    );

    expect(status.state).toBe("degraded");
    expect(status.label).not.toBe("Protection on");
    expect(status.description).toContain("enrol this install again");
  });

  it("keeps protection on when the backend was merely unreachable", () => {
    const status = protectionStatus(
      input({
        orgCode: "BLD-123456",
        hasConfig: true,
        deviceToken: TOKEN,
        lastAuth: outcome("network"),
      }),
    );

    expect(status.state).toBe("protected");
  });
});

describe("device token status", () => {
  it("explains that an unenrolled install uses the org code", () => {
    const status = deviceTokenStatus(input({ orgCode: "BLD-123456" }));

    expect(status.state).toBe("absent");
    expect(status.message).toMatch(/organization code/i);
  });

  it("waits for the next scan before claiming anything", () => {
    const status = deviceTokenStatus(
      input({ orgCode: "BLD-123456", deviceToken: TOKEN }),
    );

    expect(status.state).toBe("unverified");
    expect(status.message).not.toMatch(/verified on last scan/i);
  });

  it("confirms a token the backend accepted", () => {
    const status = deviceTokenStatus(
      input({
        orgCode: "BLD-123456",
        deviceToken: TOKEN,
        lastAuth: outcome("ok"),
      }),
    );

    expect(status.state).toBe("verified");
    expect(status.message).toBe("Verified on last scan.");
  });

  it("names a revoked or expired token", () => {
    const status = deviceTokenStatus(
      input({
        orgCode: "BLD-123456",
        deviceToken: TOKEN,
        lastAuth: outcome("unauthorized"),
      }),
    );

    expect(status.state).toBe("rejected");
    expect(status.message).toBe(
      "This device token was rejected. Ask an admin to enrol this install again.",
    );
  });

  it("separates an unreachable backend from a rejection", () => {
    const status = deviceTokenStatus(
      input({
        orgCode: "BLD-123456",
        deviceToken: TOKEN,
        lastAuth: outcome("network"),
      }),
    );

    expect(status.state).toBe("unreachable");
    expect(status.message).not.toMatch(/rejected/i);
  });
});

describe("why both credentials exist", () => {
  it("narrows the org code to policy loading once a token is stored", () => {
    const hint = orgCodeHint(
      input({ orgCode: "BLD-123456", deviceToken: TOKEN }),
    );

    expect(hint.toLowerCase()).toContain(
      "used only to load the organization's policy",
    );
  });

  it("keeps the original org code hint when nothing is enrolled", () => {
    expect(orgCodeHint(input({ orgCode: "BLD-123456" }))).toContain(
      "ask an admin to enrol this device",
    );
  });

  it("nudges a code only install towards enrolment", () => {
    expect(migrationHint(input({ orgCode: "BLD-123456" }))).toBe(
      "Enrol this install with a device token to stop sharing the organization code.",
    );
  });

  it("drops the nudge once a token is stored", () => {
    expect(
      migrationHint(input({ orgCode: "BLD-123456", deviceToken: TOKEN })),
    ).toBeNull();
  });

  it("stays quiet on an install with no credential at all", () => {
    expect(migrationHint(input())).toBeNull();
  });
});
