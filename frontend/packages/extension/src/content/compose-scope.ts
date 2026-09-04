export const COMPOSE_KEY_ATTRIBUTE = "data-blade-compose";
export const BLADE_SURFACE_ATTRIBUTE = "data-blade-surface";

export const SCAN_PROGRESS_SURFACE = "scan-progress";
export const COMPOSE_NOTICE_SURFACE = "compose-notice";

let nextComposeKey = 0;

export function composeKey(compose: Element): string {
  const existing = compose.getAttribute(COMPOSE_KEY_ATTRIBUTE);
  if (existing) return existing;
  nextComposeKey += 1;
  const key = String(nextComposeKey);
  compose.setAttribute(COMPOSE_KEY_ATTRIBUTE, key);
  return key;
}

export function scopedSurfaceId(surface: string, compose: Element): string {
  return `blade-${surface}-${composeKey(compose)}`;
}

export function isBladeSurface(node: Element | null): boolean {
  return node?.closest(`[${BLADE_SURFACE_ATTRIBUTE}]`) !== null;
}

export function boundComposeStillOwns(
  bound: Element,
  input: Element,
  active: Element | null,
): boolean {
  if (bound.contains(input)) return true;
  if (!active?.isConnected) return true;
  return active === bound;
}
