import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  authApi,
  isMfaChallenge,
  type AuthResponse,
  type SwitchOrgResult,
} from "../api/auth";
import { setAccessToken } from "./api";
import { errorMessage } from "./errors";

function firstCall(mock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  const call = mock.mock.calls[0];
  if (!call) throw new Error("fetch was not called");
  return call as unknown as [string, RequestInit];
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const TARGET_SESSION: AuthResponse = {
  access_token: "target-access-token",
  expires_in: 900,
  member: {
    id: "member-in-target-org",
    email: "doctor@hospital.example",
    name: "On call",
    role: "admin",
    email_verified: true,
    mfa_enabled: true,
  },
  organization: {
    id: "target-org",
    name: "Second hospital",
    slug: "second-hospital",
    org_code: null,
    plan: "standard",
  },
};

describe("switch-org result handling", () => {
  beforeEach(() => {
    setAccessToken("current-org-token");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setAccessToken(null);
  });

  it("recognises an mfa challenge so no session is applied", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(200, {
          mfa_required: true,
          challenge_token: "challenge-for-target-member",
          expires_in: 300,
        }),
      ),
    );

    const result: SwitchOrgResult = await authApi.switchOrg("target-org");

    expect(isMfaChallenge(result)).toBe(true);
    if (!isMfaChallenge(result)) throw new Error("expected a challenge");
    expect(result.challenge_token).toBe("challenge-for-target-member");
    expect(result).not.toHaveProperty("access_token");
  });

  it("recognises a session when the target org has no mfa", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(200, TARGET_SESSION)),
    );

    const result = await authApi.switchOrg("target-org");

    expect(isMfaChallenge(result)).toBe(false);
    if (isMfaChallenge(result)) throw new Error("expected a session");
    expect(result.organization.id).toBe("target-org");
    expect(result.access_token).toBe("target-access-token");
  });

  it("posts the target org id to the switch-org endpoint", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, TARGET_SESSION));
    vi.stubGlobal("fetch", fetchMock);

    await authApi.switchOrg("target-org");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = firstCall(fetchMock);
    expect(url).toContain("/api/v1/auth/switch-org");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ org_id: "target-org" });
  });

  it("surfaces the refusal when either membership is unverified", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(403, {
          detail: "no verified active membership in that org",
        }),
      ),
    );

    const err = await authApi
      .switchOrg("victim-org")
      .catch((e: unknown) => e as unknown);

    expect(errorMessage(err, "Could not switch organization")).toBe(
      "no verified active membership in that org",
    );
  });

  it("completes the switch by verifying the challenge for the target org", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, TARGET_SESSION));
    vi.stubGlobal("fetch", fetchMock);

    const session = await authApi.mfaVerify(
      "challenge-for-target-member",
      "123456",
    );

    expect(session.organization.id).toBe("target-org");
    const [url] = firstCall(fetchMock);
    expect(url).toContain("/api/v1/auth/mfa/verify");
  });
});
