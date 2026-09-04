export const CONFIG_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export function isConfigFresh(
  fetchedAt: number | undefined,
  now: number = Date.now(),
): boolean {
  if (typeof fetchedAt !== "number" || !Number.isFinite(fetchedAt))
    return false;
  return now - fetchedAt <= CONFIG_MAX_AGE_MS;
}
