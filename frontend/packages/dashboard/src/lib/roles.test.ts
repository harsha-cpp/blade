import { describe, expect, it } from "vitest";
import { can, isRole, navFor, NAV_ENTRIES, ROLES } from "./roles";

describe("can", () => {
  it("lets owners do everything the UI offers", () => {
    for (const entry of NAV_ENTRIES) {
      if (entry.capability) expect(can("owner", entry.capability)).toBe(true);
    }
  });

  it("keeps the org code away from analysts and viewers, matching the API", () => {
    expect(can("owner", "viewOrgCode")).toBe(true);
    expect(can("admin", "viewOrgCode")).toBe(true);
    expect(can("analyst", "viewOrgCode")).toBe(false);
    expect(can("viewer", "viewOrgCode")).toBe(false);
  });

  it("reserves org-code regeneration for the owner", () => {
    expect(can("owner", "regenerateOrgCode")).toBe(true);
    expect(can("admin", "regenerateOrgCode")).toBe(false);
  });

  it("lets analysts review quarantine but not edit policy", () => {
    expect(can("analyst", "reviewQuarantine")).toBe(true);
    expect(can("analyst", "editPolicy")).toBe(false);
    expect(can("viewer", "reviewQuarantine")).toBe(false);
  });

  it("lets analysts add domains but not delete them", () => {
    expect(can("analyst", "editDomains")).toBe(true);
    expect(can("analyst", "deleteDomains")).toBe(false);
  });

  it("lets every role read the pages the API lets every role read", () => {
    for (const role of ROLES) {
      expect(can(role, "viewPolicy")).toBe(true);
      expect(can(role, "viewDevices")).toBe(true);
      expect(can(role, "viewMembers")).toBe(true);
      expect(can(role, "enrollDevice")).toBe(true);
    }
  });

  it("keeps the mutations owner and admin only, matching the API", () => {
    for (const capability of [
      "editPolicy",
      "revokeDevice",
      "manageMembers",
    ] as const) {
      expect(can("owner", capability)).toBe(true);
      expect(can("admin", capability)).toBe(true);
      expect(can("analyst", capability)).toBe(false);
      expect(can("viewer", capability)).toBe(false);
    }
  });

  it("denies everything when the role is unknown", () => {
    expect(can(null, "viewAnalytics")).toBe(false);
    expect(can(undefined, "viewAudit")).toBe(false);
  });
});

describe("navFor", () => {
  it("never shows a viewer a link that would only 403", () => {
    const paths = navFor("viewer").map((e) => e.to);
    expect(paths).not.toContain("/quarantine");
  });

  it("gives a viewer every page the API lets it read", () => {
    const paths = navFor("viewer").map((e) => e.to);
    expect(paths).toEqual(
      expect.arrayContaining([
        "/",
        "/audit",
        "/domains",
        "/settings",
        "/policy",
        "/devices",
        "/members",
      ]),
    );
  });

  it("gates each page on the same capability its route does", () => {
    const byPath = Object.fromEntries(
      NAV_ENTRIES.map((entry) => [entry.to, entry.capability]),
    );
    expect(byPath["/policy"]).toBe("viewPolicy");
    expect(byPath["/devices"]).toBe("viewDevices");
    expect(byPath["/members"]).toBe("viewMembers");
    expect(byPath["/quarantine"]).toBe("reviewQuarantine");
  });

  it("gives an analyst quarantine as well as the read-only pages", () => {
    const paths = navFor("analyst").map((e) => e.to);
    expect(paths).toContain("/quarantine");
    expect(paths).toContain("/members");
    expect(paths).toContain("/policy");
  });

  it("gives an admin the full nav apart from the platform group", () => {
    expect(navFor("admin")).toHaveLength(NAV_ENTRIES.length - 1);
  });

  it("keeps the platform group out until the probe says otherwise", () => {
    expect(navFor("owner").map((e) => e.to)).not.toContain("/access-requests");
    expect(navFor("owner", { platform: true }).map((e) => e.to)).toContain(
      "/access-requests",
    );
  });

  it("never shows the platform group to a role the API would refuse", () => {
    for (const role of ["admin", "analyst", "viewer"] as const) {
      expect(navFor(role, { platform: false }).map((e) => e.to)).not.toContain(
        "/access-requests",
      );
    }
  });

  it("shows nothing role-gated when signed out", () => {
    expect(navFor(null).every((e) => e.capability === undefined)).toBe(true);
  });
});

describe("isRole", () => {
  it("accepts the four real roles and nothing else", () => {
    for (const role of ROLES) expect(isRole(role)).toBe(true);
    expect(isRole("superuser")).toBe(false);
    expect(isRole(null)).toBe(false);
  });
});
