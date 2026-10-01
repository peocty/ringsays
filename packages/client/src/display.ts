import type { IntentDisplay, Slot } from "./types";

/**
 * Presentation rules shared by the RingSays app and the enterprise SDK, so both show an intent the
 * same way. Server text is already in the requested language; these decide emphasis and trust cues.
 */

/** Verified badge only for organisation level verification. Never for consumer or phone only. */
export function isVerifiedOrganisation(i: Pick<IntentDisplay, "verification_level">): boolean {
  return i.verification_level === "ORG" || i.verification_level === "ORG_AGENT_NUMBER";
}

/** Free text from a consumer is shown in an unverified style and never as the headline. */
export function headline(i: IntentDisplay): { text: string; verified: boolean } {
  if (i.why) return { text: i.why, verified: true };
  return { text: i.unverified_subject ?? "", verified: false };
}

export function isUrgent(i: Pick<IntentDisplay, "priority">): boolean {
  return i.priority === "URGENT";
}

/** Minutes until the intent expires (0 when past). */
export function minutesLeft(i: Pick<IntentDisplay, "valid_until">, now = Date.now()): number {
  return Math.max(0, Math.floor((Date.parse(i.valid_until) - now) / 60_000));
}

/** Slots the caller offered that have not started yet, earliest first. */
export function openSlots(i: Pick<IntentDisplay, "proposed_slots">, now = Date.now()): Slot[] {
  return (i.proposed_slots ?? [])
    .filter((s) => Date.parse(s.start) > now)
    .sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
}

/** Hours (on the phone's clock, which is the customer's time zone) a suggested call may start. */
export const SUGGEST_FROM_HOUR = 9;
export const SUGGEST_UNTIL_HOUR = 20;

/**
 * Up to three alternative times the receiver can propose, at least an hour from now and two hours
 * apart, each starting between 09:00 and 20:00 local time (outside that, the next morning at 09:00).
 * Each is as long as the expected call; times are aligned to the quarter hour. None ends after
 * `deadline` (so the list can be empty: then only other answers are possible).
 */
export function suggestedSlots(durationMin: number, now = Date.now(), count = 3, deadline?: string | null): Slot[] {
  const quarter = 15 * 60_000;
  const length = Math.max(durationMin, 5) * 60_000;
  const inHours = (t: number): number => {
    const d = new Date(t);
    if (d.getHours() >= SUGGEST_FROM_HOUR && d.getHours() < SUGGEST_UNTIL_HOUR) return t;
    if (d.getHours() >= SUGGEST_UNTIL_HOUR) d.setDate(d.getDate() + 1);
    d.setHours(SUGGEST_FROM_HOUR, 0, 0, 0);
    return d.getTime();
  };
  let start = inHours(Math.ceil((now + 60 * 60_000) / quarter) * quarter);
  const latest = deadline ? Date.parse(deadline) : Infinity;
  const out: Slot[] = [];
  for (let i = 0; i < count; i += 1) {
    if (start + length > latest) break; // never suggest a time the intent's deadline rules out
    out.push({ start: new Date(start).toISOString(), end: new Date(start + length).toISOString() });
    start = inHours(start + 2 * 60 * 60_000);
  }
  return out;
}
