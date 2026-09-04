import { request } from "../lib/api";

export interface SignupStatus {
  open_signup: boolean;
}

export const publicApi = {
  signupStatus: () =>
    request<SignupStatus>("/api/v1/public/signup-status", { skipAuth: true }),
};
