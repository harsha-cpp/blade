import { readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { Plugin, ResolvedConfig } from "vite";

export interface WebAccessibleResource {
  matches?: string[];
  resources: string[];
  use_dynamic_url?: boolean;
}

export interface HardenableManifest {
  web_accessible_resources?: WebAccessibleResource[];
}

export const PDF_WORKER_RESOURCE = /^assets\/pdf\.worker[.\w-]*\.m?js$/;

function isNarrowResource(resource: string): boolean {
  if (resource.includes("*")) return false;
  return !resource.endsWith(".map");
}

export function hardenWebAccessibleResources(
  entries: readonly WebAccessibleResource[],
): WebAccessibleResource[] {
  return entries
    .map((entry) => ({
      ...entry,
      resources: entry.resources.filter(isNarrowResource),
      use_dynamic_url: false,
    }))
    .filter((entry) => entry.resources.length > 0);
}

export function hardenManifest(
  manifest: HardenableManifest,
): HardenableManifest {
  if (!manifest.web_accessible_resources) return manifest;
  return {
    ...manifest,
    web_accessible_resources: hardenWebAccessibleResources(
      manifest.web_accessible_resources,
    ),
  };
}

async function removeSourceMaps(dir: string): Promise<void> {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) await removeSourceMaps(path);
    else if (entry.name.endsWith(".map")) await rm(path, { force: true });
  }
}

export function hardenDist(): Plugin {
  let config: ResolvedConfig;

  return {
    name: "blade:harden-dist",
    apply: "build",
    configResolved(resolved) {
      config = resolved;
    },
    async closeBundle() {
      const outDir = resolve(config.root, config.build.outDir);
      try {
        await stat(outDir);
      } catch {
        return;
      }

      const manifestPath = join(outDir, "manifest.json");
      const raw = await readFile(manifestPath, "utf8");
      const hardened = hardenManifest(JSON.parse(raw) as HardenableManifest);
      await writeFile(manifestPath, `${JSON.stringify(hardened, null, 2)}\n`);

      await rm(join(outDir, ".vite"), { recursive: true, force: true });
      if (!config.build.sourcemap) await removeSourceMaps(outDir);
    },
  };
}
