import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  hardenManifest,
  hardenWebAccessibleResources,
  PDF_WORKER_RESOURCE,
  type HardenableManifest,
} from "../build-hardening";

const DIST = join(import.meta.dirname, "..", "dist");
const DIST_MANIFEST = join(DIST, "manifest.json");
const hasBuild = existsSync(DIST_MANIFEST);

describe("hardening the generated manifest", () => {
  it("drops the assets wildcard but keeps the pdf worker", () => {
    const hardened = hardenWebAccessibleResources([
      {
        matches: ["https://mail.google.com/*"],
        resources: [
          "assets/*",
          "assets/pdf.worker.min-iDqQPrd3.mjs",
          "assets/index.ts-Sb0tUx0l.js",
        ],
        use_dynamic_url: false,
      },
    ]);

    expect(hardened[0]?.resources).toEqual([
      "assets/pdf.worker.min-iDqQPrd3.mjs",
      "assets/index.ts-Sb0tUx0l.js",
    ]);
  });

  it("never leaves a source map web accessible", () => {
    const hardened = hardenWebAccessibleResources([
      {
        matches: ["https://mail.google.com/*"],
        resources: ["assets/index.js", "assets/index.js.map"],
      },
    ]);

    expect(hardened[0]?.resources).toEqual(["assets/index.js"]);
  });

  it("keeps static urls so content script imports resolve", () => {
    const hardened = hardenWebAccessibleResources([
      { matches: ["https://mail.google.com/*"], resources: ["assets/a.js"] },
      { matches: ["https://*/*"], resources: ["assets/b.js"] },
    ]);

    expect(hardened.map((entry) => entry.use_dynamic_url)).toEqual([
      false,
      false,
    ]);
  });

  it("drops an entry that has nothing left to expose", () => {
    expect(
      hardenWebAccessibleResources([
        { matches: ["https://mail.google.com/*"], resources: ["assets/*"] },
      ]),
    ).toEqual([]);
  });

  it("leaves a manifest without web accessible resources alone", () => {
    const manifest: HardenableManifest = {};
    expect(hardenManifest(manifest)).toBe(manifest);
  });
});

describe.skipIf(!hasBuild)("the built extension in dist", () => {
  const manifest = hasBuild
    ? (JSON.parse(readFileSync(DIST_MANIFEST, "utf8")) as HardenableManifest)
    : { web_accessible_resources: [] };
  const entries = manifest.web_accessible_resources ?? [];

  it("exposes no wildcard and no source map to any page", () => {
    const resources = entries.flatMap((entry) => entry.resources);

    expect(resources.filter((r) => r.includes("*"))).toEqual([]);
    expect(resources.filter((r) => r.endsWith(".map"))).toEqual([]);
  });

  it("uses a dynamic url for every entry", () => {
    expect(entries.length).toBeGreaterThan(0);
    for (const entry of entries) expect(entry.use_dynamic_url).toBe(false);
  });

  it("still lets Gmail load the pdf worker it ships", () => {
    const gmail = entries.find((entry) =>
      (entry.matches ?? []).includes("https://mail.google.com/*"),
    );
    const worker = (gmail?.resources ?? []).find((r) =>
      PDF_WORKER_RESOURCE.test(r),
    );

    expect(worker).toBeDefined();
    expect(existsSync(join(DIST, worker ?? ""))).toBe(true);
  });

  it("ships no source maps and no vite metadata directory", () => {
    const stray: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name);
        if (entry.isDirectory()) walk(path);
        else if (entry.name.endsWith(".map")) stray.push(path);
      }
    };
    walk(DIST);

    expect(stray).toEqual([]);
    expect(existsSync(join(DIST, ".vite"))).toBe(false);
  });
});
