"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, CONTACT_EMAIL } from "@/lib/api";

const LIMITS = {
  organization: { min: 2, max: 200 },
  name: { min: 1, max: 120 },
  phone: { max: 32 },
  role: { max: 80 },
  message: { max: 2000 },
} as const;

const EMAIL_PATTERN = /^[^\s@.]+(?:\.[^\s@.]+)*@[^\s@.]+(?:\.[^\s@.]+)+$/;

const HONEYPOT_INPUT_NAME = "ra-ref-2";

const SIZE_OPTIONS: [string, string][] = [
  ["under-100", "Under 100 beds"],
  ["100-500", "100 to 500 beds"],
  ["500-1000", "500 to 1000 beds"],
  ["over-1000", "Over 1000 beds"],
];

type FieldName =
  | "organization"
  | "name"
  | "email"
  | "phone"
  | "role"
  | "size"
  | "message";

type FormValues = Record<FieldName | "website", string>;

type FieldErrors = Partial<Record<FieldName, string>>;

type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success" }
  | { kind: "error"; reason: "validation" | "rate-limit" | "unavailable" };

const EMPTY_VALUES: FormValues = {
  organization: "",
  name: "",
  email: "",
  phone: "",
  role: "",
  size: "",
  message: "",
  website: "",
};

const FIELD_ERRORS: Record<FieldName, string> = {
  organization: "Enter the hospital or group name, 2 to 200 characters.",
  name: "Enter your name, up to 120 characters.",
  email: "Enter a work email address we can reply to.",
  phone: "Keep the phone number to 32 characters or fewer.",
  role: "Keep the role to 80 characters or fewer.",
  size: "Choose one of the listed ranges.",
  message: "Keep the message to 2000 characters or fewer.",
};

function isFieldName(value: string): value is FieldName {
  return Object.prototype.hasOwnProperty.call(FIELD_ERRORS, value);
}

function validate(values: FormValues): FieldErrors {
  const errors: FieldErrors = {};

  const organization = values.organization.trim();
  if (
    organization.length < LIMITS.organization.min ||
    organization.length > LIMITS.organization.max
  ) {
    errors.organization = FIELD_ERRORS.organization;
  }

  const name = values.name.trim();
  if (name.length < LIMITS.name.min || name.length > LIMITS.name.max) {
    errors.name = FIELD_ERRORS.name;
  }

  const email = values.email.trim();
  if (!EMAIL_PATTERN.test(email)) {
    errors.email = FIELD_ERRORS.email;
  }

  if (values.phone.trim().length > LIMITS.phone.max) {
    errors.phone = FIELD_ERRORS.phone;
  }

  if (values.role.trim().length > LIMITS.role.max) {
    errors.role = FIELD_ERRORS.role;
  }

  const knownSizes = SIZE_OPTIONS.map(([value]) => value);
  if (values.size !== "" && !knownSizes.includes(values.size)) {
    errors.size = FIELD_ERRORS.size;
  }

  if (values.message.trim().length > LIMITS.message.max) {
    errors.message = FIELD_ERRORS.message;
  }

  return errors;
}

function rejectedFields(body: unknown): FieldErrors {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(detail)) return {};

  const errors: FieldErrors = {};
  for (const item of detail) {
    const loc = (item as { loc?: unknown }).loc;
    if (!Array.isArray(loc)) continue;
    for (const part of loc) {
      const name = String(part);
      if (isFieldName(name)) errors[name] = FIELD_ERRORS[name];
    }
  }
  return errors;
}

function optional(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

const fieldShell =
  "w-full border border-stone/60 bg-ink-2 px-4 py-3 font-rsr text-[16px] leading-[24px] tracking-[-0.32px] text-cream placeholder:text-stone focus:border-blue focus:outline-2 focus:outline-offset-0 focus:outline-blue disabled:opacity-60";

const labelShell = "font-rsm text-[16px] leading-[24px] tracking-[-0.32px] text-cream";

const hintShell =
  "font-rsr text-[14px] leading-[22px] tracking-[-0.28px] text-stone";

const errorShell =
  "font-rsr text-[14px] leading-[22px] tracking-[-0.28px] text-blue";

type FieldProps = {
  id: FieldName;
  label: string;
  required?: boolean;
  hint?: string;
  error?: string;
  children: (props: {
    id: string;
    className: string;
    describedBy: string | undefined;
    invalid: boolean;
  }) => ReactNode;
};

function Field({ id, label, required, hint, error, children }: FieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="flex w-full flex-col gap-2">
      <label htmlFor={id} className={labelShell}>
        {label}
        {required ? (
          <span className="text-blue" aria-hidden>
            {" *"}
          </span>
        ) : (
          <span className={`ml-2 ${hintShell}`}>Optional</span>
        )}
      </label>
      {hint ? (
        <p id={hintId} className={hintShell}>
          {hint}
        </p>
      ) : null}
      {children({
        id,
        className: `${fieldShell} ${error ? "border-blue" : ""}`,
        describedBy,
        invalid: Boolean(error),
      })}
      {error ? (
        <p id={errorId} className={errorShell}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

export default function RequestAccessForm() {
  const [values, setValues] = useState<FormValues>(EMPTY_VALUES);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [state, setState] = useState<SubmitState>({ kind: "idle" });
  const statusRef = useRef<HTMLDivElement>(null);

  const submitting = state.kind === "submitting";
  const settled = state.kind === "success" || state.kind === "error";

  useEffect(() => {
    if (settled) statusRef.current?.focus();
  }, [settled, state]);

  function update(field: keyof FormValues, value: string) {
    setValues((current) => ({ ...current, [field]: value }));

    if (field === "website" || errors[field] === undefined) return;
    const remaining = { ...errors };
    delete remaining[field];
    setErrors(remaining);
    if (
      Object.keys(remaining).length === 0 &&
      state.kind === "error" &&
      state.reason === "validation"
    ) {
      setState({ kind: "idle" });
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;

    const found = validate(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setState({ kind: "error", reason: "validation" });
      return;
    }

    setState({ kind: "submitting" });

    try {
      const response = await fetch(api.accessRequests, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          organization: values.organization.trim(),
          name: values.name.trim(),
          email: values.email.trim(),
          phone: optional(values.phone),
          role: optional(values.role),
          size: optional(values.size),
          message: optional(values.message),
          website: optional(values.website),
        }),
      });

      if (response.ok) {
        setState({ kind: "success" });
        setValues(EMPTY_VALUES);
        return;
      }
      if (response.status === 422) {
        const body: unknown = await response.json().catch(() => null);
        setErrors(rejectedFields(body));
        setState({ kind: "error", reason: "validation" });
        return;
      }
      if (response.status === 429) {
        setState({ kind: "error", reason: "rate-limit" });
        return;
      }
      setState({ kind: "error", reason: "unavailable" });
    } catch {
      setState({ kind: "error", reason: "unavailable" });
    }
  }

  if (state.kind === "success") {
    return (
      <div
        ref={statusRef}
        role="status"
        tabIndex={-1}
        className="flex w-full max-w-[640px] flex-col gap-4 border border-blue bg-ink-2 px-6 py-8 focus:outline-2 focus:outline-offset-2 focus:outline-blue"
      >
        <p className="eyebrow text-blue">Request received</p>
        <p className="body-20 text-cream">
          Thanks. We will be in touch within two working days.
        </p>
        <p className={hintShell}>
          If it is urgent, email us at{" "}
          <a className="text-blue underline" href={`mailto:${CONTACT_EMAIL}`}>
            {CONTACT_EMAIL}
          </a>
          .
        </p>
      </div>
    );
  }

  return (
    <form
      noValidate
      onSubmit={handleSubmit}
      className="relative flex w-full max-w-[640px] flex-col gap-6"
    >
      <Field
        id="organization"
        label="Hospital or group name"
        required
        error={errors.organization}
      >
        {({ id, className, describedBy, invalid }) => (
          <input
            id={id}
            name={id}
            type="text"
            required
            autoComplete="organization"
            maxLength={LIMITS.organization.max}
            value={values.organization}
            onChange={(e) => update("organization", e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            disabled={submitting}
            className={className}
          />
        )}
      </Field>

      <Field id="name" label="Your name" required error={errors.name}>
        {({ id, className, describedBy, invalid }) => (
          <input
            id={id}
            name={id}
            type="text"
            required
            autoComplete="name"
            maxLength={LIMITS.name.max}
            value={values.name}
            onChange={(e) => update("name", e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            disabled={submitting}
            className={className}
          />
        )}
      </Field>

      <Field
        id="email"
        label="Work email"
        required
        hint="We reply to this address, so use the one your hospital issued."
        error={errors.email}
      >
        {({ id, className, describedBy, invalid }) => (
          <input
            id={id}
            name={id}
            type="email"
            required
            autoComplete="email"
            value={values.email}
            onChange={(e) => update("email", e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            disabled={submitting}
            className={className}
          />
        )}
      </Field>

      <Field id="phone" label="Phone" error={errors.phone}>
        {({ id, className, describedBy, invalid }) => (
          <input
            id={id}
            name={id}
            type="tel"
            autoComplete="tel"
            maxLength={LIMITS.phone.max}
            value={values.phone}
            onChange={(e) => update("phone", e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            disabled={submitting}
            className={className}
          />
        )}
      </Field>

      <Field
        id="role"
        label="Your role"
        hint="For example CIO, CISO, IT head or compliance."
        error={errors.role}
      >
        {({ id, className, describedBy, invalid }) => (
          <input
            id={id}
            name={id}
            type="text"
            autoComplete="organization-title"
            maxLength={LIMITS.role.max}
            value={values.role}
            onChange={(e) => update("role", e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            disabled={submitting}
            className={className}
          />
        )}
      </Field>

      <Field id="size" label="Size of the hospital" error={errors.size}>
        {({ id, className, describedBy, invalid }) => (
          <select
            id={id}
            name={id}
            value={values.size}
            onChange={(e) => update("size", e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            disabled={submitting}
            className={className}
          >
            <option value="">Select a range</option>
            {SIZE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        )}
      </Field>

      <Field
        id="message"
        label="Anything else"
        hint="Which teams send patient data today, and what you want covered first."
        error={errors.message}
      >
        {({ id, className, describedBy, invalid }) => (
          <textarea
            id={id}
            name={id}
            rows={5}
            maxLength={LIMITS.message.max}
            value={values.message}
            onChange={(e) => update("message", e.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            disabled={submitting}
            className={className}
          />
        )}
      </Field>

      <div
        aria-hidden
        className="absolute left-[-9999px] top-0 h-px w-px overflow-hidden"
      >
        <input
          id={HONEYPOT_INPUT_NAME}
          name={HONEYPOT_INPUT_NAME}
          type="text"
          tabIndex={-1}
          autoComplete="new-password"
          value={values.website}
          onChange={(e) => update("website", e.target.value)}
        />
      </div>

      {state.kind === "error" ? (
        <div
          ref={statusRef}
          role="alert"
          tabIndex={-1}
          className="border border-blue bg-ink-2 px-4 py-3 focus:outline-2 focus:outline-offset-2 focus:outline-blue"
        >
          <p className="font-rsr text-[16px] leading-[24px] tracking-[-0.32px] text-cream">
            {state.reason === "validation"
              ? Object.keys(errors).length > 0
                ? "Check the highlighted fields."
                : "We could not accept those details. Check them and try again."
              : null}
            {state.reason === "rate-limit"
              ? "Too many requests from this network. Try again in a few minutes."
              : null}
            {state.reason === "unavailable" ? (
              <>
                {"We could not send that right now. Email us instead at "}
                <a
                  className="text-blue underline"
                  href={`mailto:${CONTACT_EMAIL}`}
                >
                  {CONTACT_EMAIL}
                </a>
              </>
            ) : null}
          </p>
        </div>
      ) : null}

      <button
        type="submit"
        disabled={submitting}
        className="btn btn-primary btn-sm w-full disabled:cursor-not-allowed disabled:opacity-60 tab:w-auto tab:min-w-[220px] tab:self-start"
      >
        <span className="relative">
          {submitting ? "Sending" : "Request access"}
        </span>
      </button>
    </form>
  );
}
