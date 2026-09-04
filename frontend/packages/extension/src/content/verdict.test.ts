import { describe, expect, it } from "vitest";
import { parseVerdict } from "./verdict";

const wellFormed = {
  scan_id: "scan-1",
  action: "block",
  severity: "high",
  risk_score: 90,
  matched_policy_ids: ["local-phi-policy"],
  entities: [
    {
      type: "IN_AADHAAR",
      masked_value: "XXXX XXXX 7460",
      confidence: 0.95,
      source: "body",
    },
  ],
  recipients: [{ email: "doc@hospital.org", classification: "internal" }],
  user_message: "Blocked.",
  created_at: "2026-01-01T00:00:00Z",
};

describe("verdicts coming back from the backend", () => {
  it("accepts a well formed verdict", () => {
    expect(parseVerdict(wellFormed)?.action).toBe("block");
  });

  it.each([
    ["an unknown action", { ...wellFormed, action: "send-it" }],
    ["a missing message", { ...wellFormed, user_message: undefined }],
    ["entities that are not a list", { ...wellFormed, entities: "none" }],
    ["a risk score sent as text", { ...wellFormed, risk_score: "90" }],
    [
      "a recipient that is not an address",
      {
        ...wellFormed,
        recipients: [{ email: "nope", classification: "internal" }],
      },
    ],
    ["nothing at all", null],
    ["a bare string", "allow"],
  ])("rejects %s", (_label, body) => {
    expect(parseVerdict(body)).toBeNull();
  });

  it("keeps an optional quarantine id", () => {
    const parsed = parseVerdict({ ...wellFormed, quarantine_id: "q-1" });
    expect(parsed?.quarantine_id).toBe("q-1");
  });
});
