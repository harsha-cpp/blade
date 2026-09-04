"use client";

import Button, { type ButtonSize, type ButtonVariant } from "./Button";
import { links } from "@/lib/links";
import { useOpenSignup } from "@/lib/useOpenSignup";

type Props = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
};

export default function PrimaryCta({
  variant = "primary",
  size = "lg",
  className = "",
}: Props) {
  const openSignup = useOpenSignup();

  return (
    <Button
      href={openSignup ? links.createAccount : links.requestAccess}
      variant={variant}
      size={size}
      className={className}
    >
      {openSignup ? "Create account" : "Request access"}
    </Button>
  );
}
