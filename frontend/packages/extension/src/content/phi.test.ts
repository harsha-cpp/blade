import { describe, expect, it } from "vitest";
import { detectPhi, scorePhi, type PhiHit } from "./phi";
import { validateAadhaar, validatePan, RULE_PACK } from "@bladedlp/shared";

const types = (text: string): string[] =>
  detectPhi(text).map((hit) => hit.type);

describe("offline fallback validators", () => {
  it("accepts Aadhaar numbers with a valid Verhoeff checksum", () => {
    expect(validateAadhaar("7534 7930 7460")).toBe(true);
    expect(types("Aadhaar 7534 7930 7460")).toContain("IN_AADHAAR");
  });

  it("rejects Aadhaar numbers with an invalid checksum", () => {
    expect(validateAadhaar("2345 6789 0123")).toBe(false);
    expect(types("Aadhaar 2345 6789 0123")).not.toContain("IN_AADHAAR");
  });

  it("filters placeholder PAN values while accepting valid structure", () => {
    expect(validatePan("HKPPS5875Q")).toBe(true);
    expect(validatePan("ABCDE1234F")).toBe(false);
    expect(types("PAN HKPPS5875Q and fake ABCDE1234F")).toEqual(["IN_PAN"]);
  });
});

describe("rule pack parity with the server", () => {
  it("uses the exported pack rather than local regexes", () => {
    expect(RULE_PACK.version).toMatch(/^\d{4}\.\d{2}\.\d+$/);
    expect(RULE_PACK.rules.length).toBeGreaterThan(20);
  });

  it("names entities the way the server does", () => {
    expect(types("ABHA 12-3456-7890-1234 linked")).toContain("ABHA_NUMBER");
    expect(types("Primary diagnosis coded as N39.0 for the patient")).toContain(
      "ICD10",
    );
  });
});

describe("false-positive traps", () => {
  it("does not read a room number or a vitamin as a diagnosis", () => {
    expect(
      types("Clinical meeting in room A12 about the patient"),
    ).not.toContain("ICD10");
    expect(types("Patient advised vitamin B12 supplements")).not.toContain(
      "ICD10",
    );
  });

  it("does not read a vendor invoice as a health ID", () => {
    expect(
      types("Please process vendor invoice 12345678901234."),
    ).not.toContain("ABHA_NUMBER");
  });

  it("does not read a purchase order as a record number", () => {
    expect(
      types("Order HSP-2026-0012 for stationery has been delivered."),
    ).not.toContain("MRN");
  });

  it("does not read lowercase prose as a person", () => {
    expect(
      types("the patient in bed 7 became oliguric overnight"),
    ).not.toContain("PERSON");
  });
});

describe("overlap resolution", () => {
  it("prefers the composite identifier over fragments inside it", () => {
    const found = types("ABHA 96-9015-1720-1488 verified");
    expect(found).toContain("ABHA_NUMBER");
    expect(found).not.toContain("IN_AADHAAR");
  });
});

describe("risk scoring", () => {
  it("scores on 0-100 and reaches the thresholds policy tests for", () => {
    const clean = scorePhi(detectPhi("Can we meet in the cafeteria at noon?"));
    expect(clean.risk).toBe(0);
    expect(clean.severity).toBe("none");

    const aadhaar = scorePhi(detectPhi("Aadhaar 7534 7930 7460 on file"));
    expect(aadhaar.risk).toBeGreaterThan(50);
    expect(aadhaar.risk).toBeLessThanOrEqual(100);
    expect(aadhaar.severity).toBe("high");
  });

  it("ranks several distinct identifiers above one repeated identifier", () => {
    const repeated = scorePhi(
      detectPhi(Array(5).fill("Aadhaar 7534 7930 7460").join(" ")),
    );
    const distinct = scorePhi(
      detectPhi(
        [
          "7534 7930 7460",
          "7919 8193 9197",
          "8534 4210 7545",
          "2199 2000 1839",
          "9552 4877 0811",
        ]
          .map((value) => `Aadhaar ${value}`)
          .join(" "),
      ),
    );
    expect(repeated.risk).toBeLessThan(distinct.risk);
  });
});

describe("identifiers that mask to the same string", () => {
  const first: PhiHit = {
    type: "IN_AADHAAR",
    masked_value: "**********7460",
    confidence: 0.95,
    source: "body",
    value_hash: "aaaa1111",
  };
  const second: PhiHit = { ...first, value_hash: "bbbb2222" };
  const repeat: PhiHit = { ...first };

  it("carries a value fingerprint out of the detector", () => {
    const hit = detectPhi("Aadhaar 7534 7930 7460").find(
      (candidate) => candidate.type === "IN_AADHAAR",
    );

    expect(hit).toBeDefined();
    expect(hit?.value_hash).toBeTruthy();
  });

  it("scores two distinct Aadhaars ending in the same digits as two subjects", () => {
    const two = detectPhi("Aadhaar 2000 0005 7460 and Aadhaar 2000 0011 7460");
    const twice = detectPhi(
      "Aadhaar 2000 0005 7460 and Aadhaar 2000 0005 7460",
    );

    expect(two).toHaveLength(2);
    expect(two[0]?.masked_value).toBe(two[1]?.masked_value);
    expect(two[0]?.value_hash).not.toBe(two[1]?.value_hash);
    expect(scorePhi(two).risk).toBeGreaterThan(scorePhi(twice).risk);
  });

  it("scores two distinct patients as two subjects, not one repeat", () => {
    expect(scorePhi([first, second]).risk).toBeGreaterThan(
      scorePhi([first, repeat]).risk,
    );
  });

  it("falls back to the masked value when no fingerprint is carried", () => {
    const { value_hash: _dropped, ...bare } = first;

    expect(scorePhi([bare, { ...bare }]).risk).toBe(
      scorePhi([first, repeat]).risk,
    );
  });
});
