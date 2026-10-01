/** Runtime configuration from build time environment. Defaults are for local development with MOCK sign in. */
export const config = {
  apiBase: import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000",
  oidcAuthority: import.meta.env.VITE_OIDC_AUTHORITY ?? "http://127.0.0.1:8000/dev/oidc",
  oidcClientId: import.meta.env.VITE_OIDC_CLIENT_ID ?? "ringsays-portal",
  /** Refresh interval for the live intent monitor. */
  monitorRefreshMs: 5000,
} as const;
