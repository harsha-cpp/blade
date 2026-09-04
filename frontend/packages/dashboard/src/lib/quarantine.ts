import { ApiError } from "./api";
import { errorMessage } from "./errors";

export const ALREADY_DECIDED_MESSAGE =
  "Someone else already decided this item.";

export function isAlreadyDecided(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409;
}

export function decisionErrorMessage(err: unknown): string {
  if (isAlreadyDecided(err)) return ALREADY_DECIDED_MESSAGE;
  return errorMessage(err, "Could not record that decision.");
}
