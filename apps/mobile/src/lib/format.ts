import type { Lang } from "../i18n";

/** Gregorian calendar and Latin digits, phone's time zone. */
const LOCALE: Record<Lang, string> = { ar: "ar-SA-u-ca-gregory-nu-latn", en: "en-GB" };

export function formatTime(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat(LOCALE[lang], { weekday: "short", hour: "numeric", minute: "2-digit", day: "numeric", month: "short" }).format(d);
}

export function formatClock(iso: string, lang: Lang): string {
  return new Intl.DateTimeFormat(LOCALE[lang], { hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}

/** Left to right isolate for numbers, codes and phone numbers inside Arabic text. */
export function ltr(s: string): string {
  return `⁦${s}⁩`;
}
