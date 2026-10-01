/** Inlined at build time. Defaults talk to the developer's machine (local MOCK environment). */
export const config = {
  bankApi: process.env.EXPO_PUBLIC_BANK_API || "http://127.0.0.1:4100",
  ringsaysApi: process.env.EXPO_PUBLIC_RINGSAYS_API || "http://127.0.0.1:8000",
};
