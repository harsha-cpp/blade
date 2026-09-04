import { request } from "../lib/api";

export interface AuditEvent {
  id: string;
  actor: string;
  category: string;
  action: string;
  metadata: Record<string, unknown>;
  previous_hash: string | null;
  event_hash: string;
  created_at: string;
}

export interface AuditPage {
  events: AuditEvent[];
  next_cursor: string | null;
}

export type ChainBreakKind = "chain" | "row_hmac";

export interface ChainStatus {
  ok: boolean;
  checked: number;
  legacy: number;
  broken_at: number | null;
  failed_check: ChainBreakKind | null;
  detail: string | null;
  verified_at: string;
  cached: boolean;
}

export interface AuditQuery {
  search?: string | undefined;
  category?: string | undefined;
  actor?: string | undefined;
  action?: string | undefined;
  since?: string | undefined;
  until?: string | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
}

export const auditApi = {
  list: (query: AuditQuery = {}) =>
    request<AuditPage>("/api/v1/audit", {
      query: {
        search: query.search?.trim() || undefined,
        category: query.category || undefined,
        actor: query.actor?.trim() || undefined,
        action: query.action || undefined,
        since: query.since || undefined,
        until: query.until || undefined,
        cursor: query.cursor || undefined,
        limit: query.limit ?? 50,
      },
    }),

  categories: () => request<string[]>("/api/v1/audit/categories"),

  chain: () => request<ChainStatus>("/api/v1/audit/chain"),
};
