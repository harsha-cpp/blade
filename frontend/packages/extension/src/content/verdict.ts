import { verdictSchema, type Verdict } from "@bladedlp/shared";

export function parseVerdict(body: unknown): Verdict | null {
  const parsed = verdictSchema.safeParse(body);
  return parsed.success ? parsed.data : null;
}
