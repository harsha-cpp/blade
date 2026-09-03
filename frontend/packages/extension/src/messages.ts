import { z } from "zod";

export const BACKEND_REQUEST_MESSAGE = "BACKEND_REQUEST";

export const ATTACHMENT_INLINE_MAX_BYTES = 25 * 1024 * 1024;

const timeoutMsSchema = z.number().int().positive().max(120_000);

const eventEntitySchema = z.object({
  type: z.string(),
  confidence: z.number(),
});

const eventRequestSchema = z.object({
  type: z.literal(BACKEND_REQUEST_MESSAGE),
  kind: z.literal("event"),
  timeoutMs: timeoutMsSchema,
  clientEventId: z.string().min(1),
  userEmail: z.string().nullable(),
  action: z.string().min(1),
  severity: z.string().min(1),
  riskScore: z.number(),
  entities: z.array(eventEntitySchema),
  recipients: z.array(z.string()),
  timestamp: z.string(),
});

const attachmentUploadRequestSchema = z.object({
  type: z.literal(BACKEND_REQUEST_MESSAGE),
  kind: z.literal("attachment-upload"),
  timeoutMs: timeoutMsSchema,
  clientScanId: z.string().min(1),
  attachmentId: z.string().min(1),
  filename: z.string().min(1),
  mimeType: z.string().min(1),
  dataBase64: z.string(),
});

const attachmentStatusRequestSchema = z.object({
  type: z.literal(BACKEND_REQUEST_MESSAGE),
  kind: z.literal("attachment-status"),
  timeoutMs: timeoutMsSchema,
  attachmentScanId: z.string().min(1),
});

const scanFinalizeRequestSchema = z.object({
  type: z.literal(BACKEND_REQUEST_MESSAGE),
  kind: z.literal("scan-finalize"),
  timeoutMs: timeoutMsSchema,
  clientScanId: z.string().min(1),
  subject: z.string(),
  body: z.string(),
  recipients: z.array(z.string()),
  userEmail: z.string().nullable(),
  attachmentScanIds: z.array(z.string()),
});

const quarantineStatusRequestSchema = z.object({
  type: z.literal(BACKEND_REQUEST_MESSAGE),
  kind: z.literal("quarantine-status"),
  timeoutMs: timeoutMsSchema,
  quarantineId: z.string().min(1),
});

export const backendRequestSchema = z.discriminatedUnion("kind", [
  eventRequestSchema,
  attachmentUploadRequestSchema,
  attachmentStatusRequestSchema,
  scanFinalizeRequestSchema,
  quarantineStatusRequestSchema,
]);

export type BackendRequest = z.infer<typeof backendRequestSchema>;
export type BackendEventRequest = z.infer<typeof eventRequestSchema>;
export type BackendAttachmentUploadRequest = z.infer<
  typeof attachmentUploadRequestSchema
>;

export type BackendFailureKind = "network" | "timeout" | "http";

export type BackendResult =
  | { ok: true; status: number; body: unknown }
  | {
      ok: false;
      kind: BackendFailureKind;
      status?: number | undefined;
      detail?: string | undefined;
    };

export function parseBackendRequest(message: unknown): BackendRequest | null {
  const parsed = backendRequestSchema.safeParse(message);
  return parsed.success ? parsed.data : null;
}

const BASE64_CHUNK_BYTES = 0x8000;

export function encodeBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += BASE64_CHUNK_BYTES) {
    const chunk = bytes.subarray(offset, offset + BASE64_CHUNK_BYTES);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

export function decodeBase64(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(value);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
