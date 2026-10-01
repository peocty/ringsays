import { Session, SoftwareDeviceKey, type DeviceSigner } from "@ringsays/client";
import { getRandomBytes } from "expo-crypto";

import { config } from "../config";
import { currentLang } from "../i18n";
import { deviceSecrets } from "./secureStore";

const DEVICE_KEY = "ringsays.devicekey";

/**
 * Device key for this sign in. A new key is made after every sign out, so two accounts used on one
 * phone are not linkable through a shared key. An unreadable key (Keystore invalidated after a lock
 * screen change, for example) is replaced; the person signs in again.
 */
async function deviceKey(): Promise<DeviceSigner> {
  let stored: string | null = null;
  try {
    stored = await deviceSecrets.get(DEVICE_KEY);
  } catch {
    await deviceSecrets.delete(DEVICE_KEY).catch(() => undefined);
  }
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
    const p = deviceKey().then(
      (signer) =>
        new Session({
          baseUrl: config.apiBase,
          store: deviceSecrets,
          signer,
          language: currentLang,
          onSignedOut: () => {
            void forgetDevice();
            signedOutListeners.forEach((fn) => fn());
          },
        }),
    );
    // A failed start (secure storage unavailable) is retried on the next call, not cached.
    p.catch(() => {
      if (session === p) session = null;
    });
    session = p;
  }
  return session;
}

/** Drop the device key and the session object; the next sign in makes a new key. */
export async function forgetDevice(): Promise<void> {
  session = null;
  await deviceSecrets.delete(DEVICE_KEY);
}

/** Sign out on the server (device and push tokens revoked) and on the phone, then forget the key. */
export async function signOutEverywhere(): Promise<void> {
  try {
    await (await getSession()).signOut();
  } finally {
    await forgetDevice().catch(() => undefined);
  }
}
