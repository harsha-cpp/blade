import { describe, expect, it } from "vitest";
import { DEVICE_TOKEN_STORAGE_KEY } from "./device-token";

const sources = import.meta.glob("./**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const CONTENT_SCRIPT_ENTRIES = [
  "./content/index.ts",
  "./content/input-protection-entry.ts",
];

const SPECIFIER = /(?:from\s*|import\s*\(?\s*)["']([^"']+)["']/g;

function resolve(fromModule: string, specifier: string): string | null {
  if (!specifier.startsWith(".")) return null;
  const base = fromModule.slice(0, fromModule.lastIndexOf("/"));
  const parts = `${base}/${specifier}`.split("/");
  const stack: string[] = [];
  for (const part of parts) {
    if (part === "." || part === "") continue;
    if (part === "..") stack.pop();
    else stack.push(part);
  }
  const path = `./${stack.join("/")}`;
  for (const candidate of [path, `${path}.ts`, `${path}.tsx`]) {
    if (candidate in sources) return candidate;
  }
  return null;
}

function mentionsDeviceToken(source: string): boolean {
  return (
    source.includes(DEVICE_TOKEN_STORAGE_KEY) ||
    source.includes("DEVICE_TOKEN_STORAGE_KEY")
  );
}

function contentScriptModules(): string[] {
  const seen = new Set<string>();
  const queue = [...CONTENT_SCRIPT_ENTRIES];
  while (queue.length > 0) {
    const current = queue.pop();
    if (!current || seen.has(current)) continue;
    seen.add(current);
    const source = sources[current];
    if (source === undefined) continue;
    for (const match of source.matchAll(SPECIFIER)) {
      const target = resolve(current, match[1] ?? "");
      if (target && !seen.has(target)) queue.push(target);
    }
  }
  return [...seen];
}

describe("device token reach", () => {
  it("sees both content script entry points", () => {
    const modules = contentScriptModules();

    for (const entry of CONTENT_SCRIPT_ENTRIES) {
      expect(modules).toContain(entry);
    }
    expect(modules.length).toBeGreaterThan(CONTENT_SCRIPT_ENTRIES.length);
  });

  it("keeps the token out of every module a page can host", () => {
    const leaking = contentScriptModules().filter((module) =>
      mentionsDeviceToken(sources[module] ?? ""),
    );

    expect(leaking).toEqual([]);
  });

  it("keeps the token header out of every module a page can host", () => {
    const leaking = contentScriptModules().filter((module) =>
      (sources[module] ?? "").includes("X-Blade-Device-Token"),
    );

    expect(leaking).toEqual([]);
  });

  it("reads the token only in the service worker and the popup", () => {
    const readers = Object.keys(sources).filter(
      (module) =>
        !module.endsWith(".test.ts") &&
        !module.endsWith(".test.tsx") &&
        module !== "./device-token.ts" &&
        mentionsDeviceToken(sources[module] ?? ""),
    );

    expect(readers.sort()).toEqual([
      "./background/credentials.ts",
      "./popup/index.tsx",
    ]);
  });
});
