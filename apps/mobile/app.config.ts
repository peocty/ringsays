import type { ConfigContext, ExpoConfig } from "expo/config";

/**
 * Per environment values on top of app.json. The app reads EXPO_PUBLIC_RINGSAYS_API_BASE (inlined
 * at build time); it is also copied here so native builds expose it in their manifest.
 * EXPO_PUBLIC_RINGSAYS_PREVIEW_PUSH_TOKEN is for web preview builds against the MOCK push provider.
 * GOOGLE_SERVICES_JSON: path to google-services.json for Android push (FCM).
 * Layout direction: the app sets it from its own language (Yoga `direction`), so native RTL is off
 * (`supportsRTL: false`) and an Arabic phone with the app in English stays consistently left to right.
 */
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...(config as ExpoConfig),
  // FCM needs the Firebase project file on Android (kept out of the repository, provided by EAS secret).
  android: {
    ...config.android,
    ...(process.env.GOOGLE_SERVICES_JSON ? { googleServicesFile: process.env.GOOGLE_SERVICES_JSON } : {}),
  },
  extra: {
    ...config.extra,
    apiBase: process.env.EXPO_PUBLIC_RINGSAYS_API_BASE ?? config.extra?.apiBase,
  },
});
