import { p256 } from "@noble/curves/nist.js";

import { fromBase64, toBase64, utf8 } from "./base64";

/**
 * The device's sign in key. The server stores the public key at sign in and requires every refresh
 * token to be signed by it (ECDSA P-256, SHA-256, DER signature, base64), so a stolen refresh token
 * is useless without the phone.
 *
 * `SoftwareDeviceKey` keeps the secret key in the app's secure storage (iOS Keychain, Android
 * Keystore backed EncryptedSharedPreferences through expo-secure-store). A hardware key that never
 * leaves the Secure Enclave or StrongBox implements the same interface; see docs/STATUS.md.
 */
export interface DeviceSigner {
  /** Base64 DER SubjectPublicKeyInfo, as the API expects. */
  publicKeySpki(): string;
  /** Base64 DER ECDSA P-256 signature over SHA-256 of the UTF-8 message. */
  sign(message: string): Promise<string>;
}

/** DER prefix of SubjectPublicKeyInfo for id-ecPublicKey with prime256v1, followed by the 65 byte point. */
const SPKI_P256_PREFIX = new Uint8Array([
  0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01, 0x06, 0x08, 0x2a, 0x86, 0x48, 0xce,
  0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
]);

export function spkiFromPoint(uncompressed: Uint8Array): Uint8Array {
  if (uncompressed.length !== 65 || uncompressed[0] !== 0x04) throw new Error("expected uncompressed P-256 point");
  const out = new Uint8Array(SPKI_P256_PREFIX.length + 65);
  out.set(SPKI_P256_PREFIX, 0);
  out.set(uncompressed, SPKI_P256_PREFIX.length);
  return out;
}

export type RandomBytes = (n: number) => Uint8Array;

export class SoftwareDeviceKey implements DeviceSigner {
  private constructor(private readonly secret: Uint8Array) {}

  /** `random` must be a cryptographic random source (expo-crypto getRandomBytes on device). */
  static generate(random: RandomBytes): SoftwareDeviceKey {
    // Seed of 48 bytes; noble reduces it to a valid scalar without bias (FIPS 186-5 A.2.1).
    return new SoftwareDeviceKey(p256.utils.randomSecretKey(random(48)));
  }

  static fromExport(b64: string): SoftwareDeviceKey {
    const secret = fromBase64(b64);
    if (secret.length !== 32) throw new Error("bad device key");
    return new SoftwareDeviceKey(secret);
  }

  /** Secret key for secure storage. Never sent anywhere. */
  export(): string {
    return toBase64(this.secret);
  }

  publicKeySpki(): string {
    return toBase64(spkiFromPoint(p256.getPublicKey(this.secret, false)));
  }

  async sign(message: string): Promise<string> {
    return toBase64(p256.sign(utf8(message), this.secret, { format: "der", prehash: true, lowS: true }));
  }
}
