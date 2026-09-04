import { describe, expect, it } from "vitest";

import {
  detect,
  maskValue,
  scoreEntities,
  valueFingerprint,
  RULE_PACK,
  type DetectedEntity,
} from "./engine";
import { fold, originalSpan } from "./normalization";
import { validateIcd10, validateIfscKnownBank } from "./validators";

const DEVANAGARI_AADHAAR =
  "\u096d\u096b\u0969\u096a \u096d\u096f\u0969\u0966 \u096d\u096a\u096c\u0966";
const DEVANAGARI_AADHAAR_ALT =
  "\u0968\u096e\u096a\u096c \u0967\u0966\u096b\u0969 \u096d\u096a\u096c\u0966";
const DEVANAGARI_AADHAAR_LABEL = "\u0906\u0927\u093e\u0930";
const ZERO_WIDTH_JOINER = "\u200d";
const PAPERCLIP_EMOJI = "\u{1f4ce}";
const FAMILY_EMOJI = "\u{1f468}\u200d\u{1f469}\u200d\u{1f467}";

const icd10Categories = new Set(RULE_PACK.icd10Categories);
const ifscBankCodes = new Set(RULE_PACK.ifscBankCodes);

describe("masking", () => {
  it("keeps the legacy shape when no entity type is given", () => {
    expect(maskValue("753479307460")).toBe("********7460");
    expect(maskValue("abc")).toBe("***");
    expect(maskValue("   ")).toBe("");
  });

  it("masks names, dates of birth and diagnosis codes in full", () => {
    expect(maskValue("E11.9", "ICD10")).toBe("*****");
    expect(maskValue("14/07/1976", "DATE_OF_BIRTH")).toBe("**********");
    expect(maskValue("Ramesh Kumar Iyer", "PERSON")).toBe(
      "*****************",
    );
  });

  it("keeps only the domain of an email address", () => {
    expect(maskValue("ramesh.iyer88@apollo.in", "EMAIL_ADDRESS")).toBe(
      "*************@apollo.in",
    );
    expect(maskValue("@apollo.in", "EMAIL_ADDRESS")).toBe("**********");
  });

  it("shows at most a third of a numeric identifier", () => {
    expect(maskValue("0024518", "MRN")).toBe("*****18");
    expect(maskValue("012345", "MRN")).toBe("****45");
    expect(maskValue("0123", "MRN")).toBe("***3");
    expect(maskValue("7534 7930 7460", "IN_AADHAAR")).toBe("**********7460");
  });
});

describe("scoring dedupe", () => {
  function entity(
    type: string,
    masked: string,
    value: string,
  ): DetectedEntity {
    return {
      type,
      masked_value: masked,
      value_hash: valueFingerprint(type, value),
      confidence: 1,
      source: "body",
      start: 0,
      end: masked.length,
    };
  }

  it("counts two identifiers sharing a masked tail separately", () => {
    const distinct = scoreEntities([
      entity("IN_AADHAAR", "**********7460", "7534 7930 7460"),
      entity("IN_AADHAAR", "**********7460", "2846 1053 7460"),
    ]);
    const repeated = scoreEntities([
      entity("IN_AADHAAR", "**********7460", "7534 7930 7460"),
      entity("IN_AADHAAR", "**********7460", "7534 7930 7460"),
    ]);
    expect(distinct.risk).toBeGreaterThan(repeated.risk);
  });

  it("counts two fully masked names of the same length separately", () => {
    const distinct = scoreEntities([
      entity("PERSON", "************", "Ramesh Kumar"),
      entity("PERSON", "************", "Sunita Bhatt"),
    ]);
    const repeated = scoreEntities([
      entity("PERSON", "************", "Ramesh Kumar"),
      entity("PERSON", "************", "Ramesh Kumar"),
    ]);
    expect(distinct.risk).toBeGreaterThan(repeated.risk);
  });

  it("counts two devanagari aadhaars as two distinct values", () => {
    expect(
      valueFingerprint("IN_AADHAAR", DEVANAGARI_AADHAAR),
    ).not.toBe(valueFingerprint("IN_AADHAAR", DEVANAGARI_AADHAAR_ALT));
    const distinct = scoreEntities([
      entity("IN_AADHAAR", "**********7460", DEVANAGARI_AADHAAR),
      entity("IN_AADHAAR", "**********7460", DEVANAGARI_AADHAAR_ALT),
    ]);
    const repeated = scoreEntities([
      entity("IN_AADHAAR", "**********7460", DEVANAGARI_AADHAAR),
      entity("IN_AADHAAR", "**********7460", DEVANAGARI_AADHAAR),
    ]);
    expect(distinct.risk).toBeGreaterThan(repeated.risk);
  });

  it("falls back to the masked value when no hash is carried", () => {
    const scored = scoreEntities([
      {
        type: "MRN",
        masked_value: "*****18",
        confidence: 1,
        source: "body",
        start: 0,
        end: 7,
      },
    ]);
    expect(scored.risk).toBeGreaterThan(0);
  });
});

describe("icd10 validation", () => {
  it("accepts WHO codes used in India", () => {
    expect(validateIcd10("I64", icd10Categories)).toBe(true);
    expect(validateIcd10("E14", icd10Categories)).toBe(true);
    expect(validateIcd10("E14.9", icd10Categories)).toBe(true);
    expect(validateIcd10("E11.9", icd10Categories)).toBe(true);
  });

  it("still rejects junk codes", () => {
    expect(validateIcd10("E99.999", icd10Categories)).toBe(false);
    expect(validateIcd10("D91.0", icd10Categories)).toBe(false);
    expect(validateIcd10("E11.", icd10Categories)).toBe(false);
    expect(validateIcd10("E11.ABCDE", icd10Categories)).toBe(false);
  });
});

describe("ifsc validation", () => {
  it("recognises bundled bank codes", () => {
    expect(validateIfscKnownBank("HDFC0001234", ifscBankCodes)).toBe(true);
    expect(validateIfscKnownBank("IBKL0001234", ifscBankCodes)).toBe(true);
    expect(validateIfscKnownBank("TEMP0000123", ifscBankCodes)).toBe(false);
  });

  it("detects an idbi bank ifsc without extra context", () => {
    expect(detect("credit IBKL0001234").map((hit) => hit.type)).toContain(
      "IN_IFSC",
    );
  });

  it("needs banking context for an unlisted prefix", () => {
    expect(
      detect("The template code TEMP0000123 was rejected by the form.").map(
        (hit) => hit.type,
      ),
    ).not.toContain("IN_IFSC");
    expect(
      detect("Credit the refund to IFSC TEMP0000123 this week.").map(
        (hit) => hit.type,
      ),
    ).toContain("IN_IFSC");
  });
});

describe("person rules", () => {
  it("does not read an institutional phrase as a person", () => {
    expect(
      detect("Department of Internal Medicine for Clinical Excellence").map(
        (hit) => hit.type,
      ),
    ).not.toContain("PERSON");
    expect(
      detect("Raise a purchase order for Medical Sciences Board today.").map(
        (hit) => hit.type,
      ),
    ).not.toContain("PERSON");
  });

  it("still reads a real name after a preposition", () => {
    expect(
      detect("Reports are ready for Ramesh Kumar Iyer as discussed.").map(
        (hit) => hit.type,
      ),
    ).toContain("PERSON");
  });

  it("does not read a product version as a date of birth", () => {
    expect(
      detect("Install Adobe Reader 11/2/2024 on the workstation.").map(
        (hit) => hit.type,
      ),
    ).not.toContain("DATE_OF_BIRTH");
    expect(
      detect("DOB 11/2/2024 was captured at admission.").map((hit) => hit.type),
    ).toContain("DATE_OF_BIRTH");
  });

  it("reports the name span, not the label span, when they repeat", () => {
    const cases: [string, string][] = [
      ["Patient Name: Patient", "Patient"],
      ["Beneficiary name: Beneficiary", "Beneficiary"],
    ];
    for (const [text, value] of cases) {
      const person = detect(text).find((hit) => hit.type === "PERSON");
      expect(person, `no PERSON detected in ${text}`).toBeDefined();
      expect(text.slice(person!.start, person!.end)).toBe(value);
      expect(person!.start).toBe(text.lastIndexOf(value));
    }
  });

  it("keeps a checksum valid aadhaar despite a weak negative term", () => {
    expect(
      detect("Sharing the number 7534 7930 7460 for your reference.").map(
        (hit) => hit.type,
      ),
    ).toContain("IN_AADHAAR");
  });

  it("drops a verhoeff valid number surrounded by commercial vocabulary", () => {
    const commercial = [
      "Invoice no 812806192057 raised against the purchase order",
      "Vendor transaction id 748233725236 was settled against the GST invoice.",
    ];
    for (const text of commercial) {
      expect(detect(text).map((hit) => hit.type), text).not.toContain(
        "IN_AADHAAR",
      );
    }
  });
});

describe("unicode folding", () => {
  it("leaves ascii text untouched", () => {
    const folded = fold("UHID 0024518");
    expect(folded.offsets).toBeNull();
    expect(originalSpan(folded, 5, 12)).toEqual([5, 12]);
  });

  it("detects an aadhaar written with devanagari digits", () => {
    const text = `Aadhaar ${DEVANAGARI_AADHAAR} issued.`;
    const hits = detect(text);
    expect(hits.map((hit) => hit.type)).toContain("IN_AADHAAR");
    const aadhaar = hits.find((hit) => hit.type === "IN_AADHAAR")!;
    expect(text.slice(aadhaar.start, aadhaar.end)).toBe(DEVANAGARI_AADHAAR);
    expect(aadhaar.masked_value.endsWith(DEVANAGARI_AADHAAR.slice(-4))).toBe(
      true,
    );
  });

  it("keeps identifiers whole across every folded invisible character", () => {
    const invisibles = [
      "\u00ad",
      "\u034f",
      "\u180e",
      "\u2060",
      "\u2061",
      "\u2064",
      "\ufe00",
      "\ufe0f",
    ];
    for (const invisible of invisibles) {
      expect(
        detect(`PAN HKP${invisible}PS5875Q filed with form 16.`).map(
          (hit) => hit.type,
        ),
      ).toContain("IN_PAN");
      expect(
        detect(`Aadhaar 7534 79${invisible}30 7460 issued.`).map(
          (hit) => hit.type,
        ),
      ).toContain("IN_AADHAAR");
    }
  });

  it("folds adlam digits, which live outside the basic plane", () => {
    const adlam = [..."753479307460"]
      .map((digit) => String.fromCodePoint(0x1e950 + Number(digit)))
      .join("");
    const text = `Aadhaar ${adlam} issued.`;
    const aadhaar = detect(text).find((hit) => hit.type === "IN_AADHAAR");
    expect(aadhaar, "no aadhaar detected in adlam digits").toBeDefined();
    expect(text.slice(aadhaar!.start, aadhaar!.end)).toBe(adlam);
  });

  it("detects a pan split by a zero width joiner", () => {
    const hits = detect(
      `PAN HKP${ZERO_WIDTH_JOINER}PS5875Q filed with form 16.`,
    );
    expect(hits.map((hit) => hit.type)).toContain("IN_PAN");
  });

  it("does not shift offsets on a non ascii page", () => {
    const text = `${DEVANAGARI_AADHAAR_LABEL} 7534 7930 7460 issued.`;
    const aadhaar = detect(text).find((hit) => hit.type === "IN_AADHAAR")!;
    expect(text.slice(aadhaar.start, aadhaar.end)).toBe("7534 7930 7460");
  });

  it("keeps utf-16 spans exact after an astral emoji", () => {
    const text = `${PAPERCLIP_EMOJI} Aadhaar 7534 7930 7460 issued.`;
    const aadhaar = detect(text).find((hit) => hit.type === "IN_AADHAAR")!;
    expect([aadhaar.start, aadhaar.end]).toEqual([11, 25]);
    expect(text.slice(aadhaar.start, aadhaar.end)).toBe("7534 7930 7460");
    expect(aadhaar.masked_value).toBe("**********7460");
  });

  it("keeps utf-16 spans exact after a zwj family emoji", () => {
    const text = `${FAMILY_EMOJI} PAN HKPPS5875Q filed with form 16.`;
    const pan = detect(text).find((hit) => hit.type === "IN_PAN")!;
    expect([pan.start, pan.end]).toEqual([13, 23]);
    expect(text.slice(pan.start, pan.end)).toBe("HKPPS5875Q");
    expect(pan.masked_value).toBe("*******75Q");
  });
});
