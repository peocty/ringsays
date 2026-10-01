import Constants from "expo-constants";

/**
 * API origin. Builds set it per environment in app config (`extra.apiBase`, for example through
 * EAS environment variables); the local default talks to the developer's machine.
 */
// EXPO_PUBLIC_ values are inlined at build time on every platform (web included).
const env = {
  apiBase: process.env.EXPO_PUBLIC_RINGSAYS_API_BASE,
  previewPushToken: process.env.EXPO_PUBLIC_RINGSAYS_PREVIEW_PUSH_TOKEN,
};

export const config = {
  apiBase: env.apiBase || (Constants.expoConfig?.extra?.apiBase as string | undefined) || "http://127.0.0.1:8000",
  appVersion: Constants.expoConfig?.version ?? "0.0.0",
  /** Web preview builds only (browser tests with the MOCK push provider). Ignored on phones. */
  previewPushToken: env.previewPushToken || null,
  /** Countries offered at sign in: KSA first (launch), then India and UAE. */
  countries: [
    { iso: "SA", dial: "+966", example: "5XXXXXXXX", national: /^5\d{8}$/ },
    { iso: "IN", dial: "+91", example: "9XXXXXXXXX", national: /^[6-9]\d{9}$/ },
    { iso: "AE", dial: "+971", example: "5XXXXXXXX", national: /^5\d{8}$/ },
  ],
} as const;

export type Country = (typeof config.countries)[number];
