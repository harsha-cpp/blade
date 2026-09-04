import { useEffect, useState } from "react";
import { accessRequestsApi } from "../api/access-requests";
import { ApiError } from "./api";
import { useAuth } from "./auth";
import type { Role } from "./roles";

export type PlatformAccess = "checking" | "allowed" | "denied";

export interface PlatformIdentity {
  role?: Role | null | undefined;
  email?: string | null | undefined;
}

const probes = new Map<string, Promise<boolean>>();

export function clearPlatformAccessProbes(): void {
  probes.clear();
}

export function probePlatformAccess(
  identity: PlatformIdentity,
): Promise<boolean> {
  const email = identity.email?.trim().toLowerCase();
  if (identity.role !== "owner" || !email) return Promise.resolve(false);

  const cached = probes.get(email);
  if (cached) return cached;

  const probe = accessRequestsApi
    .list({ limit: 1 })
    .then(() => true)
    .catch((err: unknown) => {
      const settled = err instanceof ApiError && err.status === 403;
      if (!settled) probes.delete(email);
      return false;
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
    void probePlatformAccess({ role, email }).then((allowed) => {
      if (live) setAccess(allowed ? "allowed" : "denied");
    });
    return () => {
      live = false;
    };
  }, [role, email]);

  return access;
}
