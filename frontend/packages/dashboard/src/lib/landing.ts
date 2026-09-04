const LANDING_URL =
  (import.meta.env["VITE_LANDING_URL"] as string | undefined) ?? "";

export interface RequestAccessTarget {
  href: string;
  external: boolean;
}

export function requestAccessTarget(): RequestAccessTarget {
  const base = LANDING_URL.trim().replace(/\/+$/, "");
  if (!base) return { href: "/signup", external: false };
  return { href: `${base}/request-access`, external: true };
}
