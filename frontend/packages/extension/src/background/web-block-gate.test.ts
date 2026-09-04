import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  WEB_BLOCK_DEDUPE_MAX_ENTRIES,
  WEB_BLOCK_DEDUPE_MS,
  WEB_BLOCK_RATE_BURST,
  WEB_BLOCK_RATE_WINDOW_MS,
} from "../config";
import { WEB_BLOCK_MESSAGE, type WebBlockReport } from "../web-block";
import { createWebBlockGate, senderSiteHost } from "./web-block-gate";

const SITE = "chatgpt.com";
const NOW = 1_700_000_000_000;

function report(overrides: Partial<WebBlockReport> = {}): WebBlockReport {
  return {
    type: WEB_BLOCK_MESSAGE,
    site_host: SITE,
    entities: [
      { type: "IN_AADHAAR", confidence: 0.98, masked_value: "XXXX XXXX 7460" },
    ],
    risk_score: 82,
    severity: "high",
    reason: "sensitive-data",
    ...overrides,
  };
}

function sender(url: string | undefined): chrome.runtime.MessageSender {
  return { id: "blade", ...(url === undefined ? {} : { url }) };
}

describe("web block gate", () => {
  beforeEach(() => {
    vi.spyOn(console, "debug").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("accepts a well formed report from the frame it claims to come from", () => {
    const gate = createWebBlockGate();

    expect(gate.admit(report(), sender(`https://${SITE}/chat/1`), NOW)).toEqual(
      report(),
    );
    expect(gate.stats().dropped).toBe(0);
  });

  it("drops a report whose site host is not the sender host", () => {
    const gate = createWebBlockGate();

    expect(
      gate.admit(
        report({ site_host: "mail.google.com" }),
        sender("https://ads.example.net/frame.html"),
        NOW,
      ),
    ).toBeNull();
    expect(gate.stats().dropped).toBe(1);
  });

  it("drops a subdomain claim that the sender host does not match", () => {
    const gate = createWebBlockGate();

    expect(
      gate.admit(
        report({ site_host: `evil.${SITE}` }),
        sender(`https://${SITE}/`),
        NOW,
      ),
    ).toBeNull();
  });

  it("drops a report from a frame with no resolvable url", () => {
    const gate = createWebBlockGate();

    expect(gate.admit(report(), sender(undefined), NOW)).toBeNull();
    expect(gate.admit(report(), sender("about:blank"), NOW)).toBeNull();
    expect(gate.stats().dropped).toBe(2);
  });

  const malformed: [string, Record<string, unknown>][] = [
    ["a missing site host", { site_host: undefined }],
    ["a site host carrying a path", { site_host: `${SITE}/x` }],
    [
      "a site host longer than a hostname can be",
      { site_host: "a".repeat(254) },
    ],
    ["an out of range risk score", { risk_score: 4200 }],
    ["an unknown severity", { severity: "catastrophic" }],
    ["an unknown reason", { reason: "made-up" }],
    ["a confidence above one", { entities: [{ type: "MRN", confidence: 9 }] }],
    ["entities that are not an array", { entities: "IN_AADHAAR" }],
    [
      "a masked value longer than the backend accepts",
      {
        entities: [
          { type: "MRN", confidence: 0.9, masked_value: "X".repeat(201) },
        ],
      },
    ],
  ];

  it.each(malformed)("drops %s", (_label, overrides) => {
    const gate = createWebBlockGate();
    const message = { ...report(), ...overrides };

    expect(gate.admit(message, sender(`https://${SITE}/`), NOW)).toBeNull();
    expect(gate.stats().dropped).toBe(1);
  });

  it("strips fields the schema does not declare", () => {
    const gate = createWebBlockGate();
    const admitted = gate.admit(
      { ...report(), page_url: `https://${SITE}/secret`, note: "raw text" },
      sender(`https://${SITE}/`),
      NOW,
    );

    expect(admitted).toEqual(report());
    expect(JSON.stringify(admitted)).not.toContain("raw text");
  });

  it("collapses a repeat of the same finding inside the dedupe window", () => {
    const gate = createWebBlockGate();
    const from = sender(`https://${SITE}/`);

    expect(gate.admit(report(), from, NOW)).not.toBeNull();
    expect(gate.admit(report(), from, NOW + 1_000)).toBeNull();
    expect(
      gate.admit(report(), from, NOW + WEB_BLOCK_DEDUPE_MS + 1),
    ).not.toBeNull();
  });

  it("collapses a repeat that only varies the block reason", () => {
    const gate = createWebBlockGate();
    const from = sender(`https://${SITE}/`);

    expect(gate.admit(report(), from, NOW)).not.toBeNull();
    expect(
      gate.admit(report({ reason: "inspection-limit" }), from, NOW + 10),
    ).toBeNull();
  });

  it("drops the report after the burst is spent and refills over the window", () => {
    const gate = createWebBlockGate();
    const from = sender(`https://${SITE}/`);
    const churn = (n: number): WebBlockReport =>
      report({
        entities: [
          { type: `TYPE_${n}`, confidence: 0.9, masked_value: "XXXX9931" },
        ],
      });

    for (let i = 0; i < WEB_BLOCK_RATE_BURST; i += 1) {
      expect(gate.admit(churn(i), from, NOW)).not.toBeNull();
    }

    expect(gate.admit(churn(WEB_BLOCK_RATE_BURST), from, NOW)).toBeNull();
    expect(gate.stats().dropped).toBe(1);
    expect(
      gate.admit(
        churn(WEB_BLOCK_RATE_BURST),
        from,
        NOW + WEB_BLOCK_RATE_WINDOW_MS,
      ),
    ).not.toBeNull();
  });

  it("keeps the dedupe map bounded while a page churns entity type sets", () => {
    const gate = createWebBlockGate();
    const from = sender(`https://${SITE}/`);

    for (let i = 0; i < WEB_BLOCK_DEDUPE_MAX_ENTRIES * 4; i += 1) {
      gate.admit(
        report({
          entities: [
            { type: `TYPE_${i}`, confidence: 0.9, masked_value: "XXXX9931" },
          ],
        }),
        from,
        NOW + i * WEB_BLOCK_RATE_WINDOW_MS,
      );
    }

    expect(gate.stats().tracked).toBeLessThanOrEqual(
      WEB_BLOCK_DEDUPE_MAX_ENTRIES,
    );
  });

  it("falls back to the sender origin when the frame url has no host", () => {
    expect(
      senderSiteHost({ url: "about:blank", origin: `https://${SITE}` }),
    ).toBe(SITE);
    expect(
      senderSiteHost({
        tab: { url: `https://${SITE}/` },
      } as unknown as chrome.runtime.MessageSender),
    ).toBe(SITE);
    expect(senderSiteHost({})).toBeNull();
  });
});
