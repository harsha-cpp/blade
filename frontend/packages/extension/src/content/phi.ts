import {
  detect,
  scoreEntities,
  type DetectedEntity,
  type EntitySource,
} from "@bladedlp/shared";
import type { EntityHit, Severity } from "@bladedlp/shared";

export type { EntitySource };

export interface PhiHit extends EntityHit {
  value_hash?: string | undefined;
}

export function detectPhi(
  text: string,
  source: EntitySource = "body",
  attachmentId?: string,
): PhiHit[] {
  return detect(text, source, attachmentId).map(toEntityHit);
}

function toEntityHit(entity: DetectedEntity): PhiHit {
  const hit: PhiHit = {
    type: entity.type,
    masked_value: entity.masked_value,
    confidence: entity.confidence,
    source: entity.source,
  };
  if (entity.attachment_id) hit.attachment_id = entity.attachment_id;
  if (entity.value_hash) hit.value_hash = entity.value_hash;
  return hit;
}

export function scorePhi(entities: PhiHit[]): {
  risk: number;
  severity: Severity;
} {
  return scoreEntities(
    entities.map((hit) => ({
      type: hit.type,
      masked_value: hit.masked_value,
      value_hash: hit.value_hash,
      confidence: hit.confidence,
      source: hit.source,
      attachment_id: hit.attachment_id ?? undefined,
      start: 0,
      end: 0,
    })),
  );
}

export function stripHtml(html: string): string {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  return parsed.body.textContent ?? "";
}
