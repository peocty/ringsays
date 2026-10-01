import type { SecretStore } from "@ringsays/client";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

/**
 * Device secrets (device key, session). iOS Keychain and Android Keystore through expo-secure-store,
 * readable only while unlocked and never restored to another device from a backup.
 * Web exists only for previews and browser tests: sessionStorage, cleared with the tab.
 */
const OPTIONS: SecureStore.SecureStoreOptions = { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY };

export const deviceSecrets: SecretStore = {
  async get(key) {
    if (Platform.OS === "web") {
      try {
        return window.sessionStorage.getItem(key);
      } catch {
        return null;
      }
    }
    return SecureStore.getItemAsync(key, OPTIONS);
  },
  async set(key, value) {
    if (Platform.OS === "web") {
      try {
        window.sessionStorage.setItem(key, value);
      } catch {
        /* storage blocked: session lasts for this page only */
      }
      return;
    }
    await SecureStore.setItemAsync(key, value, OPTIONS);
  },
  async delete(key) {
    if (Platform.OS === "web") {
      try {
        window.sessionStorage.removeItem(key);
      } catch {
        /* ignore */
      }
      return;
    }
    await SecureStore.deleteItemAsync(key, OPTIONS);
  },
};
