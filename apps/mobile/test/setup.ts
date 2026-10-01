// Native modules the test renderer does not have.
jest.mock("expo-secure-store", () => {
  const data = new Map<string, string>();
  return {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 1,
    getItemAsync: async (k: string) => data.get(k) ?? null,
    setItemAsync: async (k: string, v: string) => void data.set(k, v),
    deleteItemAsync: async (k: string) => void data.delete(k),
  };
});
jest.mock("expo-localization", () => ({
  getLocales: () => [{ languageCode: "ar" }],
  getCalendars: () => [{ timeZone: "Asia/Riyadh" }],
}));
