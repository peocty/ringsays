import type { KeyValueStore } from "@ringsays/react-native-sdk";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

/**
 * Where the RingSays SDK keeps its install id: Keychain / Keystore on phones (the bank would use its
 * existing secure storage), localStorage in the web preview.
 */
export const sdkStorage: KeyValueStore = {
  async getItem(key) {
    if (Platform.OS === "web") return globalThis.localStorage?.getItem(key) ?? null;
    return SecureStore.getItemAsync(key);
  },
  async setItem(key, value) {
    if (Platform.OS === "web") return void globalThis.localStorage?.setItem(key, value);
    await SecureStore.setItemAsync(key, value, { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY });
  },
};
