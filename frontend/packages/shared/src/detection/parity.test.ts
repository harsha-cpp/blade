import { describe, expect, it } from "vitest";

import { detect, RULE_PACK } from "./engine";
import fixtureJson from "./parity-fixture.json";

interface ParityEntity {
  type: string;
  start: number;
  end: number;
  maskedValue: string;
}

interface ParityCase {
  id: string;
  text: string;
  entities: ParityEntity[];
}

interface ParityFixture {
  version: string;
  serverOnlyRules: string[];
  cases: ParityCase[];
}

const fixture = fixtureJson as unknown as ParityFixture;

function detected(text: string): ParityEntity[] {
  return detect(text).map((entity) => ({
    type: entity.type,
    start: entity.start,
    end: entity.end,
    maskedValue: entity.masked_value,
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

  it("produces the same entities, spans and masked values", () => {
    const disagreements: string[] = [];
    for (const parityCase of fixture.cases) {
      const actual = detected(parityCase.text);
      if (
        JSON.stringify(actual) !== JSON.stringify(parityCase.entities)
      ) {
        disagreements.push(
          `${parityCase.id}\n  python: ${JSON.stringify(parityCase.entities)}\n  browser: ${JSON.stringify(actual)}`,
        );
      }
    }
    expect(disagreements.join("\n")).toBe("");
  });
});
