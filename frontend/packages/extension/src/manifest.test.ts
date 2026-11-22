import { describe, expect, it } from "vitest";
import { buildManifest } from "../manifest.config";
import { resolveBackendTarget } from "./backend-url";

interface ResolvedManifest {
  permissions?: string[];
  host_permissions?: string[];
  content_scripts?: { matches?: string[] }[];
}

const manifest = buildManifest(
  resolveBackendTarget("https://api.tryblade.in", false),
) as ResolvedManifest;

describe("extension manifest", () => {
  it("asks for scripting so open tabs can be reached after an update", () => {
    expect(manifest.permissions).toContain("scripting");
  });

  it("allows the extension to run OCR in an offscreen document", () => {
    expect(manifest.permissions).toEqual([
      "storage",
      "alarms",
      "scripting",
      "offscreen",
    ]);
  });

  it("does not widen host permissions to get there", () => {
    expect(manifest.host_permissions).toEqual([
      "https://mail.google.com/*",
      "https://api.tryblade.in/*",
    ]);
  });

  it("still registers both content scripts declaratively", () => {
    const matches = (manifest.content_scripts ?? []).map(
      (script: { matches?: string[] }) => script.matches,
    );

    expect(matches).toEqual([
      ["https://mail.google.com/*"],
      ["http://*/*", "https://*/*"],
    ]);
  });
});
