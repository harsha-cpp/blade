import { scorePhi } from "./phi";
import {
  installInputProtection,
  type InputProtectionDecision,
} from "./input-protection";
import { WEB_BLOCK_MESSAGE, type WebBlockReport } from "../web-block";
import { claimInjection, WEB_GUARD_MARKER } from "./injection-marker";
import { installWebAttachmentProtection } from "./web-attachment-protection";

const INSPECTION_LIMIT_SEVERITY = "medium";

function report(decision: InputProtectionDecision): void {
  if (!decision.blocked || !decision.reason) return;

  const { risk, severity } = scorePhi(decision.hits);
  const message: WebBlockReport = {
    type: WEB_BLOCK_MESSAGE,
    site_host: location.hostname,
    entities: decision.hits.map((hit) => ({
      type: hit.type,
      confidence: hit.confidence,
      masked_value: hit.masked_value,
    })),
    risk_score: decision.reason === "inspection-limit" ? 0 : risk,
    severity:
      decision.reason === "inspection-limit"
        ? INSPECTION_LIMIT_SEVERITY
        : severity,
    reason: decision.reason,
  };

  void chrome.runtime.sendMessage(message).catch(() => {});
}

if (claimInjection(WEB_GUARD_MARKER)) {
  installInputProtection({ onBlocked: report });
  installWebAttachmentProtection(report);
}
