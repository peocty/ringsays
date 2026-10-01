import { createHmac, timingSafeEqual } from "node:crypto";

/** Five minutes either side: older or future dated deliveries are refused (replay protection). */
export const TOLERANCE_S = 300;

/**
 * Verify `RingSays-Signature: t=<unix seconds>,v1=<hex>` over `${t}.${raw body}` with the endpoint
 * secret (HMAC SHA-256). Accepts any of several v1 values, so RingSays can rotate secrets.
 * Always verify the raw bytes exactly as received, before parsing JSON.
 */
export function verifySignature(raw: Buffer, header: string | undefined, secret: string, nowS: number): boolean {
  if (!header || !secret) return false;
  let t: number | null = null;
  const sigs: string[] = [];
  for (const part of header.split(",")) {
    const [k, v] = part.trim().split("=", 2);
    if (k === "t" && v && /^\d{1,12}$/.test(v)) t = Number(v);
    else if (k === "v1" && v && /^[0-9a-f]{64}$/.test(v)) sigs.push(v);
  }
  if (t === null || sigs.length === 0 || Math.abs(nowS - t) > TOLERANCE_S) return false;
  const expected = createHmac("sha256", secret).update(`${t}.`).update(raw).digest();
  return sigs.some((s) => timingSafeEqual(Buffer.from(s, "hex"), expected));
}

export function sign(raw: Buffer | string, secret: string, t: number): string {
  const v1 = createHmac("sha256", secret).update(`${t}.`).update(raw).digest("hex");
  return `t=${t},v1=${v1}`;
}

/** Payload RingSays sends (contracts: webhooks section of enterprise.yaml). */
export interface WebhookEvent {
  event_id: string;
  type: string;
  occurred_at: string;
  intent_id: string;
  status: string;
  channel_used: string | null;
  slot?: { start: string; end: string } | null;
  proposed_slots?: { start: string; end: string }[] | null;
  decline_reason?: string | null;
  outcome_code?: string | null;
}

export function parseEvent(raw: Buffer): WebhookEvent | null {
  try {
    const e = JSON.parse(raw.toString("utf8")) as Partial<WebhookEvent>;
    if (typeof e.event_id !== "string" || typeof e.intent_id !== "string" || typeof e.status !== "string") return null;
    if (typeof e.type !== "string" || typeof e.occurred_at !== "string") return null;
    return e as WebhookEvent;
  } catch {
    return null;
  }
}
