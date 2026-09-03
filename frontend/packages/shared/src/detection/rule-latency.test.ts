import { describe, expect, it } from "vitest";

import { RULE_PACK, compile, detect } from "./engine";

const FILLER_LENGTH = 5000;
const MAX_SEARCH_MS = 100;

const FILLERS: readonly (readonly [string, string])[] = [
  ["spaces", " "],
  ["newlines", "\n"],
  ["tabs", "\t"],
];

const FIXED_PREFIXES: readonly string[] = [
  "patient",
  "policy",
  "specimen",
  "mrn",
  "uhid",
  "claim",
  "dob",
  "sample",
  "accession",
  "registration",
  "visit",
  "admission",
  "medical record",
  "account holder",
  "beneficiary",
  "referring",
  "hospital no",
  "nursing council",
  "for",
  "dr",
];

function stripGroupOpeners(pattern: string): string {
  let rest = pattern;
  for (;;) {
    if (rest.startsWith("\\b")) rest = rest.slice(2);
    else if (rest.startsWith("(?:")) rest = rest.slice(3);
    else if (rest.startsWith("(")) rest = rest.slice(1);
    else return rest;
  }
}

function readLiteralRun(alternative: string): string {
  const letters: string[] = [];
  let index = 0;
  while (index < alternative.length) {
    const character = alternative[index]!;
    if (/[A-Za-z]/.test(character)) {
      letters.push(character.toLowerCase());
      index += 1;
      continue;
    }
    const expanded = /^\[([A-Za-z])([A-Za-z])\]/.exec(alternative.slice(index));
    if (expanded && expanded[1]!.toLowerCase() === expanded[2]!.toLowerCase()) {
      letters.push(expanded[1]!.toLowerCase());
      index += expanded[0].length;
      continue;
    }
    break;
  }
  return letters.join("");
}

function leadingLiterals(pattern: string): string[] {
  return stripGroupOpeners(pattern)
    .split("|")
    .map((alternative) => readLiteralRun(stripGroupOpeners(alternative)))
    .filter((literal) => literal.length >= 2);
}

interface AdversarialCase {
  label: string;
  text: string;
}

function adversarialCases(pattern: string): AdversarialCase[] {
  const prefixes = new Set([...FIXED_PREFIXES, ...leadingLiterals(pattern)]);
  const cases: AdversarialCase[] = [];
  for (const prefix of [...prefixes].sort()) {
    for (const [fillerName, filler] of FILLERS) {
      cases.push({
        label: `"${prefix}" + ${FILLER_LENGTH} ${fillerName}`,
        text: prefix + filler.repeat(FILLER_LENGTH) + "A",
      });
    }
  }
  return cases;
}

function slowestSearchMs(
  pattern: string,
  caseSensitive: boolean,
): { label: string; elapsed: number } {
  const regex = compile(pattern, caseSensitive);
  let label = "";
  let elapsed = 0;
  for (const testCase of adversarialCases(pattern)) {
    const started = performance.now();
    regex.lastIndex = 0;
    regex.exec(testCase.text);
    const took = performance.now() - started;
    if (took > elapsed) {
      elapsed = took;
      label = testCase.label;
    }
  }
  return { label, elapsed };
}

interface PatternCase {
  name: string;
  pattern: string;
  caseSensitive: boolean;
}

const PATTERN_CASES: PatternCase[] = RULE_PACK.rules.flatMap((rule) => {
  const cases: PatternCase[] = [
    { name: `${rule.name}.pattern`, pattern: rule.pattern, caseSensitive: rule.caseSensitive },
  ];
  if (rule.continuation) {
    cases.push({
      name: `${rule.name}.continuation`,
      pattern: rule.continuation,
      caseSensitive: rule.caseSensitive,
    });
  }
  return cases;
});

describe("rule pack matcher latency", () => {
  it("covers every rule in the shipped pack", () => {
    expect(PATTERN_CASES.length).toBeGreaterThanOrEqual(RULE_PACK.rules.length);
    expect(RULE_PACK.rules.length).toBeGreaterThan(0);
  });

  it.each(PATTERN_CASES)(
    "$name stays fast on flooded whitespace",
    ({ name, pattern, caseSensitive }) => {
      const { label, elapsed } = slowestSearchMs(pattern, caseSensitive);
      expect(
        elapsed,
        `${name} took ${elapsed.toFixed(1)} ms on ${label}`,
      ).toBeLessThan(MAX_SEARCH_MS);
    },
  );

  it("keeps a full detect pass under the budget on flooded whitespace", () => {
    for (const [fillerName, filler] of FILLERS) {
      for (const prefix of FIXED_PREFIXES) {
        const text = prefix + filler.repeat(FILLER_LENGTH) + "A";
        const started = performance.now();
        detect(text);
        const elapsed = performance.now() - started;
        expect(
          elapsed,
          `detect on "${prefix}" + ${FILLER_LENGTH} ${fillerName} took ${elapsed.toFixed(1)} ms`,
        ).toBeLessThan(MAX_SEARCH_MS);
      }
    }
  });

  it("still matches realistic labelled values", () => {
    const cases: [string, string, string][] = [
      ["MRN: MH-2024-0098", "MRN", "MH-2024-0098"],
      ["Policy No. HDFC/2024/778812", "INSURANCE_POLICY", "HDFC/2024/778812"],
      ["Specimen No: LAB2024991", "LAB_ACCESSION", "LAB2024991"],
      ["IP No : IP-2024-3311", "PATIENT_VISIT_ID", "IP-2024-3311"],
      ["Patient Name : Ravi Kumar", "PERSON", "Ravi Kumar"],
    ];
    for (const [text, type, value] of cases) {
      const found = detect(text);
      const hit = found.find((entity) => entity.type === type);
      expect(hit, `no ${type} detected in ${text}`).toBeDefined();
      expect(text.slice(hit!.start, hit!.end)).toBe(value);
    }
  });
});
