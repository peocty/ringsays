import type { Lang } from "../i18n";
import type { LocalisedText } from "../api/types";

/** Gregorian calendar and Latin digits in both languages, so ids, numbers and dates read the same. */
const LOCALES: Record<Lang, string> = { ar: "ar-SA-u-ca-gregory-nu-latn", en: "en-GB" };

export function formatDateTime(iso: string | null | undefined, lang: Lang, timeZone = "Asia/Riyadh"): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALES[lang], {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone,
  }).format(d);
}

export function formatNumber(n: number, lang: Lang): string {
  return new Intl.NumberFormat(LOCALES[lang]).format(n);
}

export function formatBytes(n: number, lang: Lang): string {
  const units = lang === "ar" ? ["بايت", "ك.ب", "م.ب"] : ["B", "KB", "MB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${formatNumber(Math.round(v * 10) / 10, lang)} ${units[i]}`;
}

export function pick(text: LocalisedText | null | undefined, lang: Lang): string {
  if (!text) return "—";
  return (lang === "ar" ? text.ar : text.en) || text.en || text.ar;
}

/** Wrap left to right fragments (numbers, ids, URLs) so they render correctly inside Arabic text. */
export function ltr(s: string): string {
  return `⁦${s}⁩`;
}

function zoneOffsetMs(utcMs: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(utcMs));
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return asUtc - utcMs;
}

/**
 * A wall clock time typed in a datetime-local field ("2026-10-04T10:30"), read in `timeZone`
 * (not the browser's zone), as an ISO instant. Two passes handle offset changes at DST edges.
 */
export function zonedToIso(wall: string, timeZone: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(wall);
  if (!m) return null;
  const guess = Date.UTC(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!);
  let utc = guess - zoneOffsetMs(guess, timeZone);
  utc = guess - zoneOffsetMs(utc, timeZone);
  return new Date(utc).toISOString();
}
