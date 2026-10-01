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

/**
 * Up to three alternative times the receiver can propose: next two working hours later today or
 * tomorrow morning, each as long as the expected call. Times are aligned to the quarter hour.
 */
export function suggestedSlots(durationMin: number, now = Date.now(), count = 3): Slot[] {
  const quarter = 15 * 60_000;
  let start = Math.ceil((now + 60 * 60_000) / quarter) * quarter;
  const out: Slot[] = [];
  for (let i = 0; i < count; i += 1) {
    out.push({
      start: new Date(start).toISOString(),
      end: new Date(start + Math.max(durationMin, 5) * 60_000).toISOString(),
    });
    start += 2 * 60 * 60_000;
  }
  return out;
}
