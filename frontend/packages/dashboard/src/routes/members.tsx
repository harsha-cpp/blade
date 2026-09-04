import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { membersApi, type OrgMember } from "../api/members";
import { ROLES, ROLE_DESCRIPTIONS, type Role } from "../lib/roles";
import { useAuth } from "../lib/auth";
import { errorMessage } from "../lib/errors";
import { formatDate } from "../lib/format";
import { useDialogFocus } from "../lib/use-dialog-focus";
import PageHeader from "../components/PageHeader";

export default function MembersRoute() {
  const qc = useQueryClient();
  const { member: me, can } = useAuth();
  const { data, isLoading, error } = useQuery({
    queryKey: ["members"],
    queryFn: membersApi.list,
  });
  const canManage = can("manageMembers");

  const invite = useMutation({
    mutationFn: membersApi.invite,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
  });
  const updateRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) =>
      membersApi.updateRole(id, role),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
  });
  const remove = useMutation({
    mutationFn: membersApi.remove,
    onSuccess: () => {
      setConfirmRemove(null);
      return qc.invalidateQueries({ queryKey: ["members"] });
    },
  });

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("analyst");
  const [formError, setFormError] = useState<string | null>(null);
  const [invited, setInvited] = useState<{
    email: string;
    emailSent: boolean;
  } | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<OrgMember | null>(null);
  const removeDialogRef = useDialogFocus<HTMLDivElement>(
    confirmRemove !== null,
  );

  useEffect(() => {
    if (!confirmRemove) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setConfirmRemove(null);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [confirmRemove]);

  async function onInvite(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setInvited(null);
    const target = email.trim().toLowerCase();
    try {
      const res = await invite.mutateAsync({
        email: target,
        name: name.trim() || undefined,
        role,
      });
      setInvited({ email: res.member.email, emailSent: res.email_sent });
      setEmail("");
      setName("");
    } catch (err) {
      setFormError(errorMessage(err, "Failed to invite"));
    }
  }

  function mutateRole(id: string, next: Role) {
    setRowError(null);
    updateRole.mutate(
      { id, role: next },
      { onError: (err) => setRowError(errorMessage(err)) },
    );
  }

  function removeMember(id: string) {
    setRowError(null);
    remove.mutate(id, { onError: (err) => setRowError(errorMessage(err)) });
  }

  return (
    <div>
      <PageHeader
        section="Configure"
        title="Members"
        lede="Who can sign in to this organization's dashboard, and what they can do."
      />

      {canManage && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <div>
              <h2 className="h2">Invite a teammate</h2>
              <span className="card-hint">
                They get an email with a link that is valid for 7 days.
              </span>
            </div>
          </div>
          <form
            onSubmit={onInvite}
            className="row gap-3"
            style={{ flexWrap: "wrap", alignItems: "flex-end" }}
          >
            <div className="field grow" style={{ minWidth: 220 }}>
              <label className="label" htmlFor="invite-email">
                Email
              </label>
              <input
                id="invite-email"
                className="input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="teammate@hospital.example"
              />
            </div>
            <div className="field grow" style={{ minWidth: 180 }}>
              <label className="label" htmlFor="invite-name">
                Name <span className="subtle">(optional)</span>
              </label>
              <input
                id="invite-name"
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="field" style={{ minWidth: 150 }}>
              <label className="label" htmlFor="invite-role">
                Role
              </label>
              <select
                id="invite-role"
                className="select"
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
              >
                {ROLES.filter((r) => r !== "owner").map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={invite.isPending}
            >
              {invite.isPending ? "Sending..." : "Send invite"}
            </button>
          </form>
          <p className="hint" style={{ marginTop: 8 }}>
            {ROLE_DESCRIPTIONS[role]}
          </p>
          {formError && (
            <div className="error" style={{ marginTop: 12 }}>
              {formError}
            </div>
          )}
          {invited && (
            <div
              className={`callout${invited.emailSent ? "" : " callout-warn"}`}
              style={{ marginTop: 16 }}
            >
              {invited.emailSent ? (
                <>
                  Invite emailed to <strong>{invited.email}</strong>. Blade
                  never shows the token here; it goes only to that mailbox.
                </>
              ) : (
                <>
                  <strong>{invited.email}</strong> was added, but the invite
                  email could not be sent. Check the mail configuration, then
                  re-invite. The token is not recoverable from this screen.
                </>
              )}
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2 className="h2">All members</h2>
          <span className="subtle">{data?.length ?? 0} total</span>
        </div>
        {error && (
          <div className="error" style={{ marginBottom: 12 }}>
            {errorMessage(error)}
          </div>
        )}
        {rowError && (
          <div className="error" style={{ marginBottom: 12 }}>
            {rowError}
          </div>
        )}
        {isLoading && <div className="skeleton skeleton-text" />}
        {data && data.length > 0 && (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Verified</th>
                  <th>2FA</th>
                  <th>Last sign-in</th>
                  {canManage && <th />}
                </tr>
              </thead>
              <tbody>
                {data.map((m) => (
                  <MemberRow
                    key={m.id}
                    member={m}
                    isSelf={me?.id === m.id}
                    canManage={canManage}
                    canGrantOwner={me?.role === "owner"}
                    onRole={(r) => mutateRole(m.id, r)}
                    onRemove={() => {
                      setRowError(null);
                      setConfirmRemove(m);
                    }}
                    busy={updateRole.isPending || remove.isPending}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {confirmRemove ? (
        <div className="dialog-scrim" role="presentation">
          <div
            ref={removeDialogRef}
            className="dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="remove-member-title"
          >
            <h2 className="h2" id="remove-member-title">
              Remove this member?
            </h2>
            <p className="muted">
              <strong>{confirmRemove.email}</strong> loses access to this
              dashboard straight away, and any invite they have not accepted
              stops working. What they already did stays in the audit log.
            </p>
            {rowError && (
              <div className="error" style={{ marginTop: 12 }}>
                {rowError}
              </div>
            )}
            <div
              className="row gap-2"
              style={{ marginTop: 18, justifyContent: "flex-end" }}
            >
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setConfirmRemove(null)}
                disabled={remove.isPending}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => removeMember(confirmRemove.id)}
                disabled={remove.isPending}
              >
                {remove.isPending ? "Removing..." : "Remove member"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MemberRow({
  member,
  isSelf,
  canManage,
  canGrantOwner,
  onRole,
  onRemove,
  busy,
}: {
  member: OrgMember;
  isSelf: boolean;
  canManage: boolean;
  canGrantOwner: boolean;
  onRole: (r: Role) => void;
  onRemove: () => void;
  busy: boolean;
}) {
  const roleOptions = ROLES.filter(
    (r) => r !== "owner" || canGrantOwner || member.role === "owner",
  );

  return (
    <tr>
      <td>
        {member.email}
        {isSelf && (
          <span className="subtle" style={{ marginLeft: 6 }}>
            (you)
          </span>
        )}
      </td>
      <td>{member.name || "-"}</td>
      <td>
        {canManage && !isSelf ? (
          <select
            className="select"
            aria-label={`Role for ${member.email}`}
            value={member.role}
            onChange={(e) => onRole(e.target.value as Role)}
            disabled={busy}
            style={{ width: 120 }}
          >
            {roleOptions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        ) : (
          <span className="badge">{member.role}</span>
        )}
      </td>
      <td>
        <span
          className={`action-pill ${member.status === "active" ? "action-pill-allow" : "action-pill-warn"}`}
        >
          {member.status}
        </span>
      </td>
      <td>
        <span className={member.email_verified ? "badge badge-ok" : "badge"}>
          {member.email_verified ? "Yes" : "No"}
        </span>
      </td>
      <td>
        <span className={member.mfa_enabled ? "badge badge-ok" : "badge"}>
          {member.mfa_enabled ? "On" : "Off"}
        </span>
      </td>
      <td className="subtle mono">
        {member.last_login_at ? formatDate(member.last_login_at) : "Never"}
      </td>
      {canManage && (
        <td className="text-right">
          <button
            type="button"
            className="btn btn-danger btn-sm"
            onClick={onRemove}
            disabled={busy || isSelf}
          >
            Remove
          </button>
        </td>
      )}
    </tr>
  );
}
