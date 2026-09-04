import type { BackendResult } from "./messages";

export const LAST_AUTH_OUTCOME_STORAGE_KEY = "blade_last_auth";

export type AuthOutcomeKind = "ok" | "unauthorized" | "network";

export interface AuthOutcome {
  kind: AuthOutcomeKind;
  at: number;
}

export function authOutcomeForStatus(
  status: number,
  at: number,
): AuthOutcome | null {
  if (status >= 200 && status < 300) return { kind: "ok", at };
  if (status === 401 || status === 403) return { kind: "unauthorized", at };
  return null;
}

export function authOutcomeForResult(
  result: BackendResult,
  at: number,
): AuthOutcome | null {
  if (result.ok) return authOutcomeForStatus(result.status, at);
  if (result.kind === "http") {
    return result.status === undefined
      ? null
      : authOutcomeForStatus(result.status, at);
  }
  return { kind: "network", at };
}

export function parseAuthOutcome(value: unknown): AuthOutcome | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as { kind?: unknown; at?: unknown };
  const kind = candidate.kind;
  if (kind !== "ok" && kind !== "unauthorized" && kind !== "network") {
    return null;
  }
  if (typeof candidate.at !== "number") return null;
  return { kind, at: candidate.at };
}

export async function recordAuthOutcome(
  outcome: AuthOutcome | null,
): Promise<void> {
  if (!outcome) return;
  try {
    await chrome.storage.session.set({
      [LAST_AUTH_OUTCOME_STORAGE_KEY]: outcome,
    });
  } catch {}
}

export async function readAuthOutcome(): Promise<AuthOutcome | null> {
  try {
    const stored = await chrome.storage.session.get(
      LAST_AUTH_OUTCOME_STORAGE_KEY,
    );
    return parseAuthOutcome(stored[LAST_AUTH_OUTCOME_STORAGE_KEY]);
  } catch {
    return null;
  }
}

export async function forgetAuthOutcome(): Promise<void> {
  try {
    await chrome.storage.session.remove(LAST_AUTH_OUTCOME_STORAGE_KEY);
  } catch {}
}
