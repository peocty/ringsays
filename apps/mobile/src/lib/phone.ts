import type { Country } from "../config";

/** National digits typed by the person (Arabic-Indic digits accepted) to E.164. */
export function toE164(country: Country, typed: string): string | null {
  const digits = typed
    .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
    .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06f0))
    .replace(/\D/g, "")
    .replace(/^0+/, "");
  const national = digits.startsWith(country.dial.slice(1)) ? digits.slice(country.dial.length - 1) : digits;
  return country.national.test(national) ? `${country.dial}${national}` : null;
}
