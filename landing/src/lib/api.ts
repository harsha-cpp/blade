const FALLBACK_API_URL = "http://localhost:8000";
const FALLBACK_CONTACT_EMAIL = "hello@bladedlp.io";

function normalize(raw: string | undefined, fallback: string): string {
  const value = (raw ?? "").trim() || fallback;
  return value.replace(/\/+$/, "");
}

export const API_URL = normalize(process.env.NEXT_PUBLIC_API_URL, FALLBACK_API_URL);

export const CONTACT_EMAIL = normalize(
  process.env.NEXT_PUBLIC_CONTACT_EMAIL,
  FALLBACK_CONTACT_EMAIL,
);

export const api = {
  apiUrl: API_URL,
  accessRequests: `${API_URL}/api/v1/public/access-requests`,
  signupStatus: `${API_URL}/api/v1/public/signup-status`,
} as const;

export default api;
