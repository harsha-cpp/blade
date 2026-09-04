import { request } from "../lib/api";

export type AccessRequestStatus = "new" | "contacted" | "approved" | "declined";

export type AccessRequestSize =
  | "under-100"
  | "100-500"
  | "500-1000"
  | "over-1000";

export interface AccessRequest {
  id: string;
  created_at: string;
  organization: string;
  name: string;
  email: string;
  phone: string | null;
  role: string | null;
  size: AccessRequestSize | string | null;
  message: string | null;
  status: AccessRequestStatus;
  notes: string | null;
  handled_at: string | null;
  handled_by: string | null;
}

export interface AccessRequestPage {
  items: AccessRequest[];
  next_cursor: string | null;
}

export interface AccessRequestQuery {
  status?: AccessRequestStatus | undefined;
  limit?: number | undefined;
  cursor?: string | undefined;
}

export interface AccessRequestPatch {
  status?: AccessRequestStatus;
  notes?: string;
}

export const ACCESS_REQUEST_STATUSES: readonly AccessRequestStatus[] = [
  "new",
  "contacted",
  "approved",
  "declined",
];

export const ACCESS_REQUEST_PAGE_LIMIT = 50;

export const ACCESS_REQUEST_STATUS_LABELS: Record<AccessRequestStatus, string> =
  {
    new: "New",
    contacted: "Contacted",
    approved: "Approved",
    declined: "Declined",
  };

const SIZE_LABELS: Record<AccessRequestSize, string> = {
  "under-100": "Under 100 beds",
  "100-500": "100 to 500 beds",
  "500-1000": "500 to 1000 beds",
  "over-1000": "Over 1000 beds",
};

export function sizeLabel(size: string | null | undefined): string {
  if (!size) return "Not given";
  return SIZE_LABELS[size as AccessRequestSize] ?? size;
}

export const accessRequestsApi = {
  list: (query: AccessRequestQuery = {}) =>
    request<AccessRequestPage>("/api/v1/access-requests", {
      query: {
        status: query.status || undefined,
        limit: query.limit ?? ACCESS_REQUEST_PAGE_LIMIT,
        cursor: query.cursor || undefined,
      },
    }),
  update: (id: string, patch: AccessRequestPatch) =>
    request<AccessRequest>(`/api/v1/access-requests/${id}`, {
      method: "PATCH",
      body: patch,
    }),
};
