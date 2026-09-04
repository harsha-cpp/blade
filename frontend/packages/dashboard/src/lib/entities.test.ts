import { describe, expect, it } from "vitest";
import {
  ENTITY_GROUPS,
  ENTITY_TYPES,
  entityGroup,
  entityLabel,
  entitySummary,
  entityTypesByGroup,
} from "./entities";

describe("entityLabel", () => {
  it("covers all 21 canonical types", () => {
    expect(ENTITY_TYPES).toHaveLength(21);
  });

  it("never leaks a SCREAMING_SNAKE type name to a clinician", () => {
    for (const type of ENTITY_TYPES) {
      const label = entityLabel(type);
      expect(label).not.toContain("_");
      expect(label).not.toBe(type);
    }
  });

  it.each([
    ["PATIENT_VISIT_ID", "Patient visit ID"],
    ["IN_AADHAAR", "Aadhaar number"],
    ["ABHA_ADDRESS", "ABHA address"],
    ["MRN", "Medical record number"],
    ["ICD10", "ICD-10 diagnosis code"],
    ["IN_IFSC", "IFSC code"],
    ["DATE_OF_BIRTH", "Date of birth"],
  ])('labels %s as "%s"', (type, expected) => {
    expect(entityLabel(type)).toBe(expected);
  });

  it("degrades a type the engine added before this map did", () => {
    expect(entityLabel("IN_RATION_CARD")).toBe("Ration card");
  });

  it("has an answer for missing input", () => {
    expect(entityLabel(null)).toBe("Unknown type");
    expect(entityLabel("")).toBe("Unknown type");
  });
});

describe("entitySummary", () => {
  it("drops a run of stars that says nothing about the finding", () => {
    expect(entitySummary("PERSON", "*****************")).toBe("Person name");
    expect(entitySummary("DATE_OF_BIRTH", "**********")).toBe("Date of birth");
  });

  it("keeps the part of a masked value a reviewer can act on", () => {
    expect(entitySummary("EMAIL_ADDRESS", "*************@apollo.in")).toBe(
      "Email address: *************@apollo.in",
    );
    expect(entitySummary("MRN", "*****18")).toBe(
      "Medical record number: *****18",
    );
  });

  it("shows the label alone when no masked value came back", () => {
    expect(entitySummary("IN_AADHAAR", undefined)).toBe("Aadhaar number");
    expect(entitySummary("IN_AADHAAR", "  ")).toBe("Aadhaar number");
  });
});

describe("entityGroup", () => {
  it("assigns every canonical type to exactly one group", () => {
    const grouped = ENTITY_GROUPS.flatMap((g) => entityTypesByGroup(g));
    expect(grouped).toHaveLength(ENTITY_TYPES.length);
    expect(new Set(grouped).size).toBe(ENTITY_TYPES.length);
  });

  it("returns null for an unknown type instead of inventing a group", () => {
    expect(entityGroup("NOT_A_TYPE")).toBeNull();
  });
});
