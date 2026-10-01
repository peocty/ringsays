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
