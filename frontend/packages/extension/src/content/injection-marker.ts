export const GMAIL_GUARD_MARKER = "__bladeGmailGuardInstalled";
export const WEB_GUARD_MARKER = "__bladeWebGuardInstalled";

export function claimInjection(
  marker: string,
  scope: object = globalThis,
): boolean {
  const target = scope as Record<string, unknown>;
  try {
    if (target[marker] === true) return false;
    target[marker] = true;
    return true;
  } catch {
    return true;
  }
}
