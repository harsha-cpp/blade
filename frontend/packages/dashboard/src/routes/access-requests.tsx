import { useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import {
  accessRequestsApi,
  ACCESS_REQUEST_STATUS_LABELS,
  ACCESS_REQUEST_STATUSES,
  sizeLabel,
  type AccessRequest,
  type AccessRequestStatus,
} from "../api/access-requests";
import { usePlatformAccess } from "../lib/platform-access";
import { formatTime } from "../lib/format";
import { errorMessage } from "../lib/errors";
import PageHeader from "../components/PageHeader";
import { SkeletonRows } from "../components/Skeletons";

type Filter = AccessRequestStatus | "all";

const FILTERS: Array<{ value: Filter; label: string }> = [
  { value: "all", label: "All" },
  ...ACCESS_REQUEST_STATUSES.map((value) => ({
    value,
    label: ACCESS_REQUEST_STATUS_LABELS[value],
  })),
];

export default function AccessRequestsRoute() {
  const access = usePlatformAccess();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Filter>("all");
  const [openId, setOpenId] = useState<string | null>(null);

  const {
    data,
    isLoading,
    error,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useInfiniteQuery({
    queryKey: ["access-requests", status],
    queryFn: ({ pageParam }) =>
      accessRequestsApi.list({
        status: status === "all" ? undefined : status,
        cursor: pageParam,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: access === "allowed",
  });

  const items = data?.pages.flatMap((page) => page.items) ?? [];
  const open = items.find((item) => item.id === openId) ?? null;

  const save = useMutation({
    mutationFn: (input: {
      id: string;
      status: AccessRequestStatus;
      notes: string;
    }) =>
      accessRequestsApi.update(input.id, {
        status: input.status,
        notes: input.notes,
      }),
    onSuccess: async () => {
      setOpenId(null);
      await queryClient.invalidateQueries({ queryKey: ["access-requests"] });
    },
  });

  if (access === "checking") return null;

  if (access === "denied") {
    return (
      <div className="card" style={{ maxWidth: 560 }}>
        <h1 className="h2" style={{ marginBottom: 8 }}>
          Not available for your account
        </h1>
        <p className="muted">
          Access requests belong to no organization, so only Blade platform
          operators can read them.
        </p>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        section="Platform"
        title="Access requests"
        lede="People who asked for Blade from the marketing site. Move each one through triage and leave a note for whoever picks it up next."
        actions={
          <div className="segmented" role="group" aria-label="Filter by status">
            {FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                aria-pressed={status === f.value}
                onClick={() => setStatus(f.value)}
              >
                {f.label}
              </button>
            ))}
          </div>
        }
      />

      {error ? (
        <div className="error" style={{ marginBottom: 16 }}>
          {errorMessage(error, "Failed to load access requests.")}
        </div>
      ) : null}

      <div className="card card-tight">
        {items.length === 0 && !isLoading ? (
          <div className="empty">
            <strong>Nothing here.</strong>
            <span>
              {status === "all"
                ? "No access requests have come in yet."
                : `No requests are marked ${ACCESS_REQUEST_STATUS_LABELS[status].toLowerCase()}.`}
            </span>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Organization</th>
                  <th>Contact</th>
                  <th>Role</th>
                  <th>Size</th>
                  <th>Status</th>
                  <th>Handled by</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {isLoading && items.length === 0 ? (
                  <SkeletonRows rows={6} cols={8} />
                ) : (
                  items.map((item) => (
                    <tr key={item.id}>
                      <td className="subtle mono">
                        {formatTime(item.created_at)}
                      </td>
                      <td className="truncate" style={{ maxWidth: 180 }}>
                        {item.organization}
                      </td>
                      <td className="truncate" style={{ maxWidth: 220 }}>
                        <div>{item.name}</div>
                        <div className="subtle truncate">{item.email}</div>
                      </td>
                      <td className="subtle">{item.role ?? "Not given"}</td>
                      <td className="subtle">{sizeLabel(item.size)}</td>
                      <td>
                        <StatusPill status={item.status} />
                      </td>
                      <td className="truncate subtle" style={{ maxWidth: 180 }}>
                        {item.handled_by ?? "Nobody yet"}
                      </td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => setOpenId(item.id)}
                        >
                          Review
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {hasNextPage ? (
          <div
            className="row"
            style={{ marginTop: 12, justifyContent: "center" }}
          >
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => void fetchNextPage()}
              disabled={isFetchingNextPage}
            >
              {isFetchingNextPage ? "Loading..." : "Load more"}
            </button>
          </div>
        ) : null}
      </div>

      {open ? (
        <DetailDialog
          key={open.id}
          item={open}
          saving={save.isPending}
          error={
            save.error
              ? errorMessage(save.error, "Could not save that change.")
              : null
          }
          onClose={() => setOpenId(null)}
          onSave={(status_, notes) =>
            save.mutate({ id: open.id, status: status_, notes })
          }
        />
      ) : null}
    </div>
  );
}

function DetailDialog({
  item,
  saving,
  error,
  onClose,
  onSave,
}: {
  item: AccessRequest;
  saving: boolean;
  error: string | null;
  onClose: () => void;
  onSave: (status: AccessRequestStatus, notes: string) => void;
}) {
  const [status, setStatus] = useState<AccessRequestStatus>(item.status);
  const [notes, setNotes] = useState(item.notes ?? "");

  return (
    <div className="dialog-scrim" role="presentation">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="access-request-title"
      >
        <h2 className="h2" id="access-request-title">
          {item.organization}
        </h2>
        <p className="muted">
          {item.name} - {item.email}
          {item.phone ? ` - ${item.phone}` : ""}
        </p>

        <dl className="kv-grid" style={{ marginTop: 16 }}>
          <dt>Received</dt>
          <dd>{formatTime(item.created_at)}</dd>
          <dt>Role</dt>
          <dd>{item.role ?? "Not given"}</dd>
          <dt>Size</dt>
          <dd>{sizeLabel(item.size)}</dd>
          <dt>Message</dt>
          <dd>
            {item.message ? (
              item.message
            ) : (
              <span className="subtle">They left the message blank.</span>
            )}
          </dd>
          <dt>Handled</dt>
          <dd>
            {item.handled_by ? (
              `${item.handled_by}${item.handled_at ? ` on ${formatTime(item.handled_at)}` : ""}`
            ) : (
              <span className="subtle">Nobody has picked this up yet.</span>
            )}
          </dd>
        </dl>

        <div className="field" style={{ marginTop: 16 }}>
          <label className="label" htmlFor="access-request-status">
            Status
          </label>
          <select
            id="access-request-status"
            className="select"
            value={status}
            onChange={(e) => setStatus(e.target.value as AccessRequestStatus)}
          >
            {ACCESS_REQUEST_STATUSES.map((value) => (
              <option key={value} value={value}>
                {ACCESS_REQUEST_STATUS_LABELS[value]}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label className="label" htmlFor="access-request-notes">
            Notes
          </label>
          <textarea
            id="access-request-notes"
            className="textarea"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            maxLength={2000}
            placeholder="What was said, and what happens next."
          />
        </div>

        {error && <div className="error">{error}</div>}

        <div
          className="row gap-2"
          style={{ marginTop: 18, justifyContent: "flex-end" }}
        >
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onClose}
            disabled={saving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onSave(status, notes)}
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

const STATUS_VARIANTS: Record<AccessRequestStatus, string> = {
  new: "warn",
  contacted: "quarantine",
  approved: "allow",
  declined: "block",
};

function StatusPill({ status }: { status: AccessRequestStatus }) {
  return (
    <span className={`action-pill action-pill-${STATUS_VARIANTS[status]}`}>
      {ACCESS_REQUEST_STATUS_LABELS[status]}
    </span>
  );
}
