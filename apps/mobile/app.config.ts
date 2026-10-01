import type { ConfigContext, ExpoConfig } from "expo/config";

/**
 * Per environment values on top of app.json. The app reads EXPO_PUBLIC_RINGSAYS_API_BASE (inlined
 * at build time); it is also copied here so native builds expose it in their manifest.
 * EXPO_PUBLIC_RINGSAYS_PREVIEW_PUSH_TOKEN is for web preview builds against the MOCK push provider.
 */
export default ({ config }: ConfigContext): ExpoConfig => ({
  ...(config as ExpoConfig),
  extra: {
    ...config.extra,
    apiBase: process.env.EXPO_PUBLIC_RINGSAYS_API_BASE ?? config.extra?.apiBase,
  },
});
