import { getLocales } from "expo-localization";
import i18next from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import { Platform } from "react-native";

import { deviceSecrets } from "../lib/secureStore";
import { ar } from "./ar";
import { en } from "./en";

export type Lang = "ar" | "en";
const KEY = "ringsays.lang";

/** Arabic unless the phone's first language is something else we support (KSA launch). */
function deviceLang(): Lang {
  try {
    const first = getLocales()[0]?.languageCode;
    return first === "en" ? "en" : "ar";
  } catch {
    return "ar";
  }
}

function applyWebDirection(lang: Lang): void {
  if (Platform.OS === "web" && typeof document !== "undefined") {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  }
}

export async function initI18n(): Promise<void> {
  let lang = deviceLang();
  const saved = await deviceSecrets.get(KEY).catch(() => null);
  if (saved === "ar" || saved === "en") lang = saved;
  if (!i18next.isInitialized) {
    await i18next.use(initReactI18next).init({
      resources: { en: { translation: en }, ar: { translation: ar } },
      lng: lang,
      fallbackLng: "en",
      interpolation: { escapeValue: false },
      returnNull: false,
    });
  } else {
    await i18next.changeLanguage(lang);
  }
  applyWebDirection(lang);
}

export function currentLang(): Lang {
  return i18next.language === "en" ? "en" : "ar";
}

export async function setLang(lang: Lang): Promise<void> {
  await i18next.changeLanguage(lang);
  applyWebDirection(lang);
  await deviceSecrets.set(KEY, lang).catch(() => undefined);
}

/**
 * Layout direction follows the app language, not the phone setting, and switches at once: the root
 * view sets Yoga `direction`, so every row, margin start/end and icon flips without an app restart.
 */
export function useLang(): { lang: Lang; rtl: boolean; toggle: () => void } {
  const { i18n } = useTranslation();
  const lang: Lang = i18n.language === "en" ? "en" : "ar";
  return { lang, rtl: lang === "ar", toggle: () => void setLang(lang === "ar" ? "en" : "ar") };
}
