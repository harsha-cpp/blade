import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../lib/auth";
import { ApiError } from "../lib/api";
import { errorMessage } from "../lib/errors";
import { publicApi } from "../api/public";
import AuthShell from "../components/AuthShell";

const SIGNUP_CLOSED =
  "New organizations are created by invitation. Ask your administrator for an invite, or contact Blade to request access.";

export default function SignupRoute() {
  const { signup } = useAuth();
  const navigate = useNavigate();

  const [orgName, setOrgName] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: signupStatus } = useQuery({
    queryKey: ["signup-status"],
    queryFn: () => publicApi.signupStatus(),
    staleTime: 60_000,
    retry: false,
  });
  const closedSignup = signupStatus?.open_signup === false;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signup({
        org_name: orgName,
        email,
        password,
        name: name || undefined,
      });
      navigate("/onboarding", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError(SIGNUP_CLOSED);
        return;
      }
      setError(errorMessage(err, "Could not create account"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell title="Create your organization">
      <p className="muted">
        Start protecting outgoing email from patient-data leaks in minutes.
      </p>
      {closedSignup ? (
        <div className="callout callout-warn" style={{ marginTop: 16 }}>
          {SIGNUP_CLOSED}
        </div>
      ) : null}
      <form onSubmit={onSubmit} className="col gap-4" style={{ marginTop: 24 }}>
        <div className="field">
          <label className="label" htmlFor="signup-org">
            Organization name
          </label>
          <input
            id="signup-org"
            className="input"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            required
            minLength={2}
            placeholder="Sunrise Hospital"
            autoFocus
          />
        </div>
        <div className="field">
          <label className="label" htmlFor="signup-name">
            Your name <span className="subtle">(optional)</span>
          </label>
          <input
            id="signup-name"
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="name"
          />
        </div>
        <div className="field">
          <label className="label" htmlFor="signup-email">
            Work email
          </label>
          <input
            id="signup-email"
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
          />
        </div>
        <div className="field">
          <label className="label" htmlFor="signup-password">
            Password
          </label>
          <input
            id="signup-password"
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={12}
            autoComplete="new-password"
          />
          <span className="hint">
            At least 12 characters. Common passwords are rejected.
          </span>
        </div>
        {error && <div className="error">{error}</div>}
        <button
          type="submit"
          className="btn btn-primary w-full"
          disabled={submitting}
        >
          {submitting ? "Creating..." : "Create organization"}
        </button>
      </form>
      <div className="auth-footer">
        <span className="subtle">Already have an account?</span>{" "}
        <Link to="/login">Sign in</Link>
      </div>
    </AuthShell>
  );
}
