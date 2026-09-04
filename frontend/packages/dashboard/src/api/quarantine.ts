import { request } from "../lib/api";
import type { Severity } from "../lib/risk";

export type QuarantineStatus = "pending" | "approved" | "rejected";

export type QuarantineStatusFilter = QuarantineStatus | "all";

export interface QuarantineEntity {
  type?: string;
  masked_value?: string;
  confidence?: number;
  source?: string;
  attachment_id?: string | null;
}

export interface AttachmentRef {
  filename?: string;
  attachment_id?: string;
  content_type?: string;
  size_bytes?: number;
}

export interface QuarantineItem {
  id: string;
  scan_id: string;
  client_scan_id: string;
  sender: string;
  subject: string;
  recipients: string[];
  entities: QuarantineEntity[];
  matched_policy_ids: string[];
  risk_score: number;
  severity: Severity;
  status: QuarantineStatus;
  analyst_id: string | null;
  analyst_note: string | null;
  decided_at: string | null;
  attachment_refs: AttachmentRef[];
  created_at: string;
  updated_at: string;
}

export const QUARANTINE_PAGE_LIMIT = 50;

export interface QuarantinePage {
  items: QuarantineItem[];
  next_cursor: string | null;
}

export interface QuarantineQuery {
  status?: QuarantineStatusFilter | undefined;
  limit?: number | undefined;
  cursor?: string | undefined;
}

export const quarantineApi = {
  list: (query: QuarantineQuery = {}) =>
    request<QuarantinePage>("/api/v1/quarantine", {
      query: {
        status: query.status ?? "pending",
        limit: query.limit ?? QUARANTINE_PAGE_LIMIT,
        cursor: query.cursor || undefined,
      },
    }),
  get: (id: string) => request<QuarantineItem>(`/api/v1/quarantine/${id}`),
  approve: (id: string, note?: string) =>
    request<QuarantineItem>(`/api/v1/quarantine/${id}/approve`, {
      method: "POST",
      body: { note: note ?? null },
    }),
  reject: (id: string, note?: string) =>
    request<QuarantineItem>(`/api/v1/quarantine/${id}/reject`, {
      method: "POST",
      body: { note: note ?? null },
    }),
};
