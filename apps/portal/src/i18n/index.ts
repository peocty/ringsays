import i18next from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";

import { ar } from "./ar";
import { en } from "./en";

export type Lang = "ar" | "en";
const STORAGE_KEY = "ringsays.portal.lang";

function storedLang(): Lang {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v === "en" || v === "ar") return v;
  } catch {
    /* storage unavailable: fall through to default */
  }
  return "ar"; // KSA launch: Arabic first
}

export function applyDocumentLang(lang: Lang): void {
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
}

export function initI18n(lang: Lang = storedLang()) {
  void i18next.use(initReactI18next).init({
    resources: { en: { translation: en }, ar: { translation: ar } },
    lng: lang,
    fallbackLng: "en",
    interpolation: { escapeValue: false }, // React escapes
    returnNull: false,
  });
  applyDocumentLang(lang);
  return i18next;
}

export function setLang(lang: Lang): void {
  void i18next.changeLanguage(lang);
  applyDocumentLang(lang);
  try {
    window.localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* ignore */
  }
}

export function currentLang(): Lang {
  return i18next.language === "en" ? "en" : "ar";
}

export function useLang(): { lang: Lang; toggle: () => void } {
  const { i18n } = useTranslation();
  const lang: Lang = i18n.language === "en" ? "en" : "ar";
  return { lang, toggle: () => setLang(lang === "ar" ? "en" : "ar") };
}
