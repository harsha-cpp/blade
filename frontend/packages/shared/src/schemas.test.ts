import { describe, expect, it } from "vitest";
import { verdictSchema } from "./schemas";

const FASTAPI_VERDICT = {
  scan_id: "01926b3e-8f2a-7c1d-9e4f-1a2b3c4d5e6f",
  action: "block",
  severity: "critical",
  risk_score: 91.79,
  matched_policy_ids: ["unapproved-recipient-with-phi"],
  entities: [
    {
      type: "IN_AADHAAR",
      masked_value: "**********7460",
      confidence: 0.95,
      source: "body",
      attachment_id: null,
    },
    {
      type: "MRN",
      masked_value: "*****18",
      confidence: 0.85,
      source: "attachment",
      attachment_id: "01926b3e-8f2a-7c1d-9e4f-aaaaaaaaaaaa",
    },
  ],
  recipients: [{ email: "someone@gmail.com", classification: "public_email" }],
  user_message: "Patient data cannot be sent to a personal mailbox.",
  created_at: "2026-09-04T10:00:00+00:00",
  quarantine_id: null,
  degraded: false,
};

describe("verdictSchema against the FastAPI wire shape", () => {
  it("accepts a verdict whose body entities carry attachment_id null", () => {
    const result = verdictSchema.safeParse(FASTAPI_VERDICT);
    expect(result.success).toBe(true);
    if (!result.success) return;
    expect(result.data.entities[0]?.attachment_id).toBeNull();
    expect(result.data.entities[1]?.attachment_id).toBe(
      "01926b3e-8f2a-7c1d-9e4f-aaaaaaaaaaaa",
    );
  });

  it("accepts a quarantine verdict with a quarantine id and the degraded flag", () => {
    const result = verdictSchema.safeParse({
      ...FASTAPI_VERDICT,
      action: "quarantine",
      quarantine_id: "01926b3e-8f2a-7c1d-9e4f-bbbbbbbbbbbb",
      degraded: true,
    });
    expect(result.success).toBe(true);
  });

  it("still rejects a verdict missing its action", () => {
    const { action: _dropped, ...withoutAction } = FASTAPI_VERDICT;
    expect(verdictSchema.safeParse(withoutAction).success).toBe(false);
  });
});
