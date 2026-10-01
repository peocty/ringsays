import { Session, SoftwareDeviceKey, type DeviceSigner } from "@ringsays/client";
import { getRandomBytes } from "expo-crypto";

import { config } from "../config";
import { currentLang } from "../i18n";
import { deviceSecrets } from "./secureStore";

const DEVICE_KEY = "ringsays.devicekey";

/** The device key is created once and kept for this install; signing in again reuses it. */
async function deviceKey(): Promise<DeviceSigner> {
  const stored = await deviceSecrets.get(DEVICE_KEY);
  if (stored) {
    try {
      return SoftwareDeviceKey.fromExport(stored);
    } catch {
      /* corrupt: replace */
    }
  }
  const key = SoftwareDeviceKey.generate((n) => getRandomBytes(n));
  await deviceSecrets.set(DEVICE_KEY, key.export());
  return key;
}

let session: Promise<Session> | null = null;
const signedOutListeners = new Set<() => void>();

export function onSignedOut(fn: () => void): () => void {
  signedOutListeners.add(fn);
  return () => signedOutListeners.delete(fn);
}

export function getSession(): Promise<Session> {
  if (!session) {
    session = deviceKey().then(
      (signer) =>
        new Session({
          baseUrl: config.apiBase,
          store: deviceSecrets,
          signer,
          language: currentLang,
          onSignedOut: () => signedOutListeners.forEach((fn) => fn()),
        }),
    );
  }
  return session;
}

/** Erasing the account also forgets the device key, so nothing linkable stays on the phone. */
export async function forgetDevice(): Promise<void> {
  await deviceSecrets.delete(DEVICE_KEY);
  session = null;
}
