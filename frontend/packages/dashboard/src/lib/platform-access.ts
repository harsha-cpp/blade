import { useEffect, useState } from "react";
import { accessRequestsApi } from "../api/access-requests";
import { ApiError } from "./api";
import { useAuth } from "./auth";
import type { Role } from "./roles";

export type PlatformAccess = "checking" | "allowed" | "denied";

export type PlatformProbeResult = "allowed" | "denied" | "error";

export interface PlatformIdentity {
  role?: Role | null | undefined;
  email?: string | null | undefined;
}

const probes = new Map<string, Promise<PlatformProbeResult>>();

export function clearPlatformAccessProbes(): void {
  probes.clear();
}

export function probePlatformAccess(
  identity: PlatformIdentity,
): Promise<PlatformProbeResult> {
  const email = identity.email?.trim().toLowerCase();
  if (identity.role !== "owner" || !email) return Promise.resolve("denied");

  const cached = probes.get(email);
  if (cached) return cached;

  const probe = accessRequestsApi
    .list({ limit: 1 })
    .then<PlatformProbeResult>(() => "allowed")
    .catch(
      (err: unknown): PlatformProbeResult =>
        err instanceof ApiError && err.status === 403 ? "denied" : "error",
    )
    .then((result) => {
      if (result === "error") probes.delete(email);
      return result;
    });
  probes.set(email, probe);
  return probe;
}

export function usePlatformAccess(): PlatformAccess {
  const { member } = useAuth();
  const role = member?.role;
  const email = member?.email;
  const [access, setAccess] = useState<PlatformAccess>("checking");

  useEffect(() => {
    let live = true;
    void probePlatformAccess({ role, email }).then((result) => {
      if (live) setAccess(result === "allowed" ? "allowed" : "denied");
    });
    return () => {
      live = false;
    };
  }, [role, email]);

  return access;
}
