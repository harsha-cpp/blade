"use client";

import { useEffect, useState } from "react";
import { api } from "./api";

const STATUS_TIMEOUT_MS = 3000;

let signupStatusRequest: Promise<boolean> | null = null;

async function fetchSignupStatus(): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS);

  try {
    const response = await fetch(api.signupStatus, {
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return false;
    const data: unknown = await response.json();
    return (
      typeof data === "object" &&
      data !== null &&
      (data as { open_signup?: unknown }).open_signup === true
    );
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function signupStatusOnce(): Promise<boolean> {
  signupStatusRequest ??= fetchSignupStatus();
  return signupStatusRequest;
}

export function useOpenSignup(): boolean {
  const [openSignup, setOpenSignup] = useState(false);

  useEffect(() => {
    let active = true;

    void signupStatusOnce().then((isOpen) => {
      if (active && isOpen) setOpenSignup(true);
    });

    return () => {
      active = false;
    };
  }, []);

  return openSignup;
}

export default useOpenSignup;
