import { describe, expect, it } from "vitest";
import { isScannable, scanAttachment, scanAttachments } from "./attachments";
import { buildLocalVerdict, emptyPolicy } from "./policy";

function file(name: string, type: string, body = "x"): File {
  return new File([body], name, { type });
}

const approvedPolicy = {
  ...emptyPolicy(),
  approvedDomains: new Set(["hospital.org"]),
  hasConfig: true,
};

describe("local attachment scanning", () => {
  it("does not claim to read the formats it has no extractor for", () => {
    expect(isScannable(file("summary.docx", ""))).toBe(false);
    expect(isScannable(file("patients.xlsx", ""))).toBe(false);
    expect(isScannable(file("prescription.jpg", "image/jpeg"))).toBe(false);
    expect(isScannable(file("notes.txt", "text/plain"))).toBe(true);
  });

  it("counts an unreadable format as unscanned rather than as clean", async () => {
    const result = await scanAttachment(file("summary.docx", ""));
    expect(result.entities).toEqual([]);
    expect(result.unscanned).toBe(true);
  });

  it("counts a readable text file as scanned", async () => {
    const result = await scanAttachment(
      file("rota.txt", "text/plain", "Ward rota for next week."),
    );
    expect(result.unscanned).toBe(false);
  });

  it("counts an oversized file as unscanned", () => {
    const oversized = file("huge.txt", "text/plain");
    Object.defineProperty(oversized, "size", { value: 26 * 1024 * 1024 });
    expect(isScannable(oversized)).toBe(false);
  });

  it("reports how many of a batch could not be read", async () => {
    const sweep = await scanAttachments([
      file("rota.txt", "text/plain", "Ward rota for next week."),
      file("summary.docx", ""),
      file("patients.xlsx", ""),
    ]);
    expect(sweep.unscanned).toBe(2);
  });

  it("never lets an unreadable attachment settle as a local allow", async () => {
    const sweep = await scanAttachments([file("summary.docx", "")]);
    const verdict = buildLocalVerdict({
      entities: sweep.entities,
      recipients: ["doc@hospital.org"],
      policy: approvedPolicy,
      unscannedAttachments: sweep.unscanned,
    });

    expect(verdict.action).not.toBe("allow");
    expect(verdict.matched_policy_ids).toContain("unscannable-attachment");
  });
});
