import type { AuthOutcome } from "../auth-outcome";

export type ProtectionState =
  | "protected"
  | "unlinked"
  | "unconfigured"
  | "degraded";

export interface ProtectionStatus {
  state: ProtectionState;
  label: string;
  description: string;
}

export interface ProtectionInput {
  orgCode: string | null;
  hasConfig: boolean;
  orgName: string | null;
  deviceToken: string | null;
  lastAuth: AuthOutcome | null;
}

export type DeviceTokenState =
  | "absent"
  | "unverified"
  | "verified"
  | "rejected"
  | "unreachable";

export interface DeviceTokenStatus {
  state: DeviceTokenState;
  message: string;
}

const TOKEN_REJECTED =
  "This device token was rejected. Ask an admin to enrol this install again.";

const MIGRATION_NUDGE =
  "Enrol this install with a device token to stop sharing the organization code.";

function tokenRejected(input: ProtectionInput): boolean {
  return Boolean(input.deviceToken) && input.lastAuth?.kind === "unauthorized";
}

function isLinked(input: ProtectionInput): boolean {
  return Boolean(input.deviceToken) || Boolean(input.orgCode);
}

export function protectionStatus(input: ProtectionInput): ProtectionStatus {
  if (!input.hasConfig) {
    if (!isLinked(input)) {
      return {
        state: "unlinked",
        label: "Not linked",
        description:
          "Messages with patient data are held for review until this install is linked.",
      };
    }
    return {
      state: "unconfigured",
      label: "Configuration unavailable",
      description:
        "Blade could not load the policy for this install, so messages with patient data are held for review. Check the device token or organization code with your IT team.",
    };
  }

  if (tokenRejected(input)) {
    return {
      state: "degraded",
      label: "Checking locally only",
      description: `${TOKEN_REJECTED} Until then, drafts are checked against the rules built into this install.`,
    };
  }

  return {
    state: "protected",
    label: "Protection on",
    description: input.orgName
      ? `Checking outgoing mail for ${input.orgName}.`
      : "Checking outgoing mail and attachments for patient data.",
  };
}

export function deviceTokenStatus(input: ProtectionInput): DeviceTokenStatus {
  if (!input.deviceToken) {
    return {
      state: "absent",
      message:
        "This install is not enrolled, so scans and events are authorized with the organization code.",
    };
  }
  if (input.lastAuth?.kind === "unauthorized") {
    return { state: "rejected", message: TOKEN_REJECTED };
  }
  if (input.lastAuth?.kind === "network") {
    return {
      state: "unreachable",
      message: "Blade could not reach the backend on the last scan.",
    };
  }
  if (input.lastAuth?.kind === "ok") {
    return { state: "verified", message: "Verified on last scan." };
  }
  return {
    state: "unverified",
    message: "Saved. It is used from the next scan onwards.",
  };
}

export function orgCodeHint(input: ProtectionInput): string {
  if (input.deviceToken) {
    return "Used only to load the organization's policy. Scans and events are authorized by the device token.";
  }
  return "Get the code from your dashboard, or ask an admin to enrol this device instead.";
}

export function migrationHint(input: ProtectionInput): string | null {
  if (input.deviceToken || !input.orgCode) return null;
  return MIGRATION_NUDGE;
}
