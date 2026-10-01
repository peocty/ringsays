import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";

import type { Slot } from "./ringsays.js";
import type { WebhookEvent } from "./webhook.js";

/** Fictional customers of the fictional Mock Bank. PINs are demo values, hashed like a real store would. */
export interface Customer {
  id: string;
  name: { en: string; ar: string };
  phone: string;
  language: "en" | "ar";
  pinHash: string;
}

export interface BankIntent {
  /** The bank's own operation id, used as RingSays Idempotency-Key. */
  requestId: string;
  intentId: string;
  customerId: string;
  purposeCode: string;
  priority: string;
  durationMin: number;
  createdAt: string;
  createdBy: string;
  status: string;
  channelUsed: string | null;
  scheduledSlot: Slot | null;
  proposedSlots: Slot[];
  declineReason: string | null;
  outcomeCode: string | null;
  validUntil: string;
  /**
   * Given only to the customer's own signed in app session; never to the console, never logged.
   * Demo store keeps it in a 0600 file; a bank keeps it encrypted (or only in memory) and drops it
   * once the intent ends.
   */
  contextToken: string | null;
  events: { at: string; type: string; status: string; source: "webhook" | "api" }[];
  lastEventAt: string | null;
}

interface State {
  intents: BankIntent[];
  seenEvents: string[];
}

export function hashPin(pin: string, salt = randomBytes(16).toString("hex")): string {
  return `${salt}:${scryptSync(pin, salt, 32).toString("hex")}`;
}

export function pinMatches(pin: string, stored: string): boolean {
  const [salt, hash] = stored.split(":");
  if (!salt || !hash) return false;
  return timingSafeEqual(Buffer.from(hash, "hex"), scryptSync(pin, salt, 32));
}

export const DEMO_CUSTOMERS: Omit<Customer, "pinHash">[] = [
  { id: "cus_noura", name: { en: "Noura Al Harbi", ar: "نورة الحربي" }, phone: "+966500001001", language: "ar" },
  { id: "cus_faisal", name: { en: "Faisal Al Qahtani", ar: "فيصل القحطاني" }, phone: "+966500001002", language: "ar" },
  { id: "cus_priya", name: { en: "Priya Nair", ar: "بريا ناير" }, phone: "+966500001003", language: "en" },
];
export const DEMO_PIN = "2468";

const MAX_SEEN = 5000;

export class Store {
  readonly customers: Customer[];
  private state: State = { intents: [], seenEvents: [] };

  constructor(private readonly file: string | null) {
    this.customers = DEMO_CUSTOMERS.map((c) => ({ ...c, pinHash: hashPin(DEMO_PIN) }));
    if (file) {
      try {
        this.state = JSON.parse(readFileSync(file, "utf8")) as State;
      } catch {
        /* first run */
      }
    }
  }

  customer(id: string): Customer | undefined {
    return this.customers.find((c) => c.id === id);
  }

  intents(): BankIntent[] {
    return [...this.state.intents].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  intent(id: string): BankIntent | undefined {
    return this.state.intents.find((i) => i.intentId === id);
  }

  byRequest(requestId: string): BankIntent | undefined {
    return this.state.intents.find((i) => i.requestId === requestId);
  }

  forCustomer(customerId: string): BankIntent[] {
    return this.intents().filter((i) => i.customerId === customerId);
  }

  add(i: BankIntent): void {
    this.state.intents.push(i);
    this.save();
  }

  /** Apply a RingSays API response (status as of now). */
  applyIntent(id: string, r: { status: string; channel_used: string | null; scheduled_slot: Slot | null; proposed_slots?: Slot[]; valid_until: string; updated_at: string }): BankIntent | undefined {
    const i = this.intent(id);
    if (!i) return undefined;
    if (i.lastEventAt && Date.parse(r.updated_at) < Date.parse(i.lastEventAt)) return i; // a newer webhook already applied
    if (i.status !== r.status) i.events.push({ at: r.updated_at, type: "api", status: r.status, source: "api" });
    i.status = r.status;
    i.channelUsed = r.channel_used;
    i.scheduledSlot = r.scheduled_slot;
    i.proposedSlots = r.proposed_slots ?? [];
    i.validUntil = r.valid_until;
    i.lastEventAt = r.updated_at;
    this.save();
    return i;
  }

  /** Apply a verified webhook. Returns false for duplicates (RingSays retries until it gets 2xx). */
  applyEvent(e: WebhookEvent): "applied" | "duplicate" | "unknown_intent" | "stale" {
    if (this.state.seenEvents.includes(e.event_id)) return "duplicate";
    const i = this.intent(e.intent_id);
    if (!i) return "unknown_intent";
    this.state.seenEvents.push(e.event_id);
    if (this.state.seenEvents.length > MAX_SEEN) this.state.seenEvents.splice(0, this.state.seenEvents.length - MAX_SEEN);
    i.events.push({ at: e.occurred_at, type: e.type, status: e.status, source: "webhook" });
    if (i.lastEventAt && Date.parse(e.occurred_at) < Date.parse(i.lastEventAt)) {
      this.save();
      return "stale";
    }
    i.status = e.status;
    i.channelUsed = e.channel_used ?? i.channelUsed;
    if (e.type === "intent.scheduled") i.scheduledSlot = e.slot ?? null;
    if (e.type === "intent.rescheduled") {
      i.proposedSlots = e.proposed_slots ?? [];
      i.scheduledSlot = null;
    }
    if (e.type === "intent.scheduled" || e.type === "intent.accepted") i.proposedSlots = [];
    if (e.type === "intent.declined") i.declineReason = e.decline_reason ?? null;
    if (e.type === "outcome.recorded") i.outcomeCode = e.outcome_code ?? null;
    i.lastEventAt = e.occurred_at;
    this.save();
    return "applied";
  }

  private save(): void {
    if (!this.file) return;
    mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    writeFileSync(tmp, JSON.stringify(this.state), { mode: 0o600 });
    renameSync(tmp, this.file);
  }
}
