/**
 * Configuration: runtime values from /config.js (container environment) first, then build time
 * environment, then local development defaults (MOCK sign in).
 */
interface RuntimeConfig {
  apiBase?: string;
  oidcAuthority?: string;
  oidcClientId?: string;
  oidcScope?: string;
  timeZone?: string;
}

declare global {
  interface Window {
    __RINGSAYS_CONFIG__?: RuntimeConfig;
  }
}

const runtime: RuntimeConfig = (typeof window !== "undefined" && window.__RINGSAYS_CONFIG__) || {};
const nonEmpty = (...xs: (string | undefined)[]): string | undefined => xs.find((x) => x && x.trim());

export const config = {
  apiBase: nonEmpty(runtime.apiBase, import.meta.env.VITE_API_BASE) ?? "http://127.0.0.1:8000",
  oidcAuthority: nonEmpty(runtime.oidcAuthority, import.meta.env.VITE_OIDC_AUTHORITY) ?? "http://127.0.0.1:8000/dev/oidc",
  oidcClientId: nonEmpty(runtime.oidcClientId, import.meta.env.VITE_OIDC_CLIENT_ID) ?? "ringsays-portal",
  /** offline_access asks for a refresh token, so sessions renew without a full page sign in. */
  oidcScope: nonEmpty(runtime.oidcScope) ?? "openid email profile offline_access",
  /** Times are shown and entered in this zone (KSA launch). */
  timeZone: nonEmpty(runtime.timeZone) ?? "Asia/Riyadh",
  /** Refresh interval for the live intent monitor. */
  monitorRefreshMs: 5000,
} as const;
