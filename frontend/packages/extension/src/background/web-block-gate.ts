import { z } from "zod";
import {
  WEB_BLOCK_DEDUPE_MAX_ENTRIES,
  WEB_BLOCK_DEDUPE_MS,
  WEB_BLOCK_ENTITY_TYPE_MAX_CHARS,
  WEB_BLOCK_MASKED_VALUE_MAX_CHARS,
  WEB_BLOCK_MAX_ENTITIES,
  WEB_BLOCK_RATE_BURST,
  WEB_BLOCK_RATE_WINDOW_MS,
  WEB_BLOCK_SITE_HOST_MAX_CHARS,
} from "../config";
import {
  WEB_BLOCK_MESSAGE,
  webBlockKey,
  type WebBlockReport,
} from "../web-block";

const BARE_HOSTNAME = /^[a-z0-9._:[\]-]+$/i;

const webBlockEntitySchema = z.object({
  type: z.string().min(1).max(WEB_BLOCK_ENTITY_TYPE_MAX_CHARS),
  confidence: z.number().min(0).max(1),
  masked_value: z.string().max(WEB_BLOCK_MASKED_VALUE_MAX_CHARS),
});

const webBlockReportSchema = z.object({
  type: z.literal(WEB_BLOCK_MESSAGE),
  site_host: z
    .string()
    .min(1)
    .max(WEB_BLOCK_SITE_HOST_MAX_CHARS)
    .regex(BARE_HOSTNAME),
  entities: z.array(webBlockEntitySchema).max(WEB_BLOCK_MAX_ENTITIES),
  risk_score: z.number().min(0).max(100),
  severity: z.enum(["none", "low", "medium", "high", "critical"]),
  reason: z.enum(["sensitive-data", "inspection-limit"]),
});

export function parseWebBlockReport(message: unknown): WebBlockReport | null {
  const parsed = webBlockReportSchema.safeParse(message);
  return parsed.success ? parsed.data : null;
}

export function senderSiteHost(
  sender: chrome.runtime.MessageSender,
): string | null {
  for (const candidate of [sender.url, sender.origin, sender.tab?.url]) {
    if (!candidate) continue;
    try {
      const host = new URL(candidate).hostname;
      if (host) return host.toLowerCase();
    } catch {}
  }
  return null;
}

export interface WebBlockGateStats {
  tracked: number;
  dropped: number;
}

export interface WebBlockGate {
  admit(
    message: unknown,
    sender: chrome.runtime.MessageSender,
    now?: number,
  ): WebBlockReport | null;
  stats(): WebBlockGateStats;
}

export function createWebBlockGate(): WebBlockGate {
  const seenAt = new Map<string, number>();
  let tokens = WEB_BLOCK_RATE_BURST;
  let refilledAt: number | null = null;
  let dropped = 0;

  const refill = (now: number): void => {
    if (refilledAt === null) {
      refilledAt = now;
      return;
    }
    const elapsed = now - refilledAt;
    if (elapsed <= 0) return;
    refilledAt = now;
    tokens = Math.min(
      WEB_BLOCK_RATE_BURST,
      tokens + (elapsed / WEB_BLOCK_RATE_WINDOW_MS) * WEB_BLOCK_RATE_BURST,
    );
  };

  const isRepeat = (key: string, now: number): boolean => {
    const last = seenAt.get(key);
    return last !== undefined && now - last < WEB_BLOCK_DEDUPE_MS;
  };

  const remember = (key: string, now: number): void => {
    seenAt.delete(key);
    seenAt.set(key, now);
    while (seenAt.size > WEB_BLOCK_DEDUPE_MAX_ENTRIES) {
      const oldest = seenAt.keys().next();
      if (oldest.done) break;
      seenAt.delete(oldest.value);
    }
  };

  const drop = (why: string): null => {
    dropped += 1;
    console.debug("[Blade] web block report dropped:", why, dropped);
    return null;
  };

  return {
    admit(message, sender, now = Date.now()) {
      const report = parseWebBlockReport(message);
      if (!report) return drop("malformed");

      const host = senderSiteHost(sender);
      if (!host) return drop("unknown sender frame");
      if (host !== report.site_host.toLowerCase()) {
        return drop("sender host mismatch");
      }

      const key = webBlockKey(report);
      if (isRepeat(key, now)) return null;

      refill(now);
      if (tokens < 1) return drop("rate limit");
      tokens -= 1;
      remember(key, now);
      return report;
    },
    stats() {
      return { tracked: seenAt.size, dropped };
    },
  };
}
