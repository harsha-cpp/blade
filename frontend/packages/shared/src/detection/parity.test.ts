import { describe, expect, it } from "vitest";

import { detect, RULE_PACK } from "./engine";
import fixtureJson from "./parity-fixture.json";
import { validateIcd10 } from "./validators";

const icd10Categories = new Set(RULE_PACK.icd10Categories);

interface ParityEntity {
  type: string;
  start: number;
  end: number;
  utf16Start: number;
  utf16End: number;
  maskedValue: string;
  valueHash: string | null;
  confidence: number;
}

interface ComparableEntity {
  type: string;
  start: number;
  end: number;
  maskedValue: string;
  valueHash: string | null;
  confidence: number;
}

interface ParityCase {
  id: string;
  text: string;
  entities: ParityEntity[];
}

interface ParityFixture {
  version: string;
  serverOnlyRules: string[];
  browserValidatorDivergences: string[];
  cases: ParityCase[];
}

const fixture = fixtureJson as unknown as ParityFixture;

function detected(text: string): ComparableEntity[] {
  return detect(text).map((entity) => ({
    type: entity.type,
    start: entity.start,
    end: entity.end,
    maskedValue: entity.masked_value,
    valueHash: entity.value_hash ?? null,
    confidence: entity.confidence,
  }));
}

function expected(entities: ParityEntity[]): ComparableEntity[] {
  return entities.map((entity) => ({
    type: entity.type,
    start: entity.utf16Start,
    end: entity.utf16End,
    maskedValue: entity.maskedValue,
    valueHash: entity.valueHash,
    confidence: entity.confidence,
  }));
}

describe("python and browser engines agree on the shared rule pack", () => {
  it("is generated from the committed rule pack", () => {
    expect(fixture.version).toBe(RULE_PACK.version);
    expect(fixture.cases.length).toBeGreaterThan(100);
  });

  it("documents the rules the browser engine cannot run", () => {
    expect(fixture.serverOnlyRules).toEqual(["spacy-ner", "mrn-tenant-*"]);
  });

  it("documents the validators the browser cannot reproduce", () => {
    expect(fixture.browserValidatorDivergences).toHaveLength(1);
    expect(fixture.browserValidatorDivergences[0]).toContain("icd10");
    expect(validateIcd10("E11.9", icd10Categories)).toBe(true);
    expect(validateIcd10("E11.99", icd10Categories)).toBe(true);
    expect(validateIcd10("E99.999", icd10Categories)).toBe(false);
  });

  it("carries astral cases with both code point and utf-16 spans", () => {
    const astral = fixture.cases.filter((parityCase) =>
      parityCase.id.startsWith("unicode.astral"),
    );
    expect(astral.length).toBeGreaterThanOrEqual(2);
    const shifted = astral.flatMap((parityCase) =>
      parityCase.entities.filter((entity) => entity.utf16Start !== entity.start),
    );
    expect(shifted.length).toBeGreaterThan(0);
  });

  it("produces the same entities, spans, hashes and confidences", () => {
    const disagreements: string[] = [];
    for (const parityCase of fixture.cases) {
      const actual = detected(parityCase.text);
      const wanted = expected(parityCase.entities);
      if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
        disagreements.push(
          `${parityCase.id}\n  python: ${JSON.stringify(wanted)}\n  browser: ${JSON.stringify(actual)}`,
        );
      }
    }
    expect(disagreements.join("\n")).toBe("");
  });
});
