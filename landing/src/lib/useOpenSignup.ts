"use client";

import { useEffect, useState } from "react";
import { api } from "./api";

const STATUS_TIMEOUT_MS = 3000;

export function useOpenSignup(): boolean {
  const [openSignup, setOpenSignup] = useState(false);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS);

    async function readSignupStatus() {
      try {
        const response = await fetch(api.signupStatus, {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return;
        const data: unknown = await response.json();
        const isOpen =
          typeof data === "object" &&
          data !== null &&
          (data as { open_signup?: unknown }).open_signup === true;
        if (active && isOpen) setOpenSignup(true);
      } catch {
      } finally {
        clearTimeout(timer);
      }
    }

    void readSignupStatus();

    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, []);

  return openSignup;
}

export default useOpenSignup;
