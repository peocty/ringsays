/**
 * Installation id: a random UUID created once per install and kept in the app's storage. RingSays
 * binds a Context Token to the first install that opens it. It identifies the install only, never
 * the person, and is not the RingSays device key.
 */
export interface KeyValueStore {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
}

const KEY = "ringsays.sdk.installId";

export function randomUuid(random: (n: number) => Uint8Array): string {
  const b = random(16);
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function defaultRandom(n: number): Uint8Array {
  const c = (globalThis as { crypto?: { getRandomValues?: (a: Uint8Array) => Uint8Array } }).crypto;
  if (!c?.getRandomValues) {
    throw new Error("RingSays SDK needs crypto.getRandomValues (install react-native-get-random-values) or pass random");
  }
  return c.getRandomValues(new Uint8Array(n));
}

export async function loadInstallId(store: KeyValueStore, random: (n: number) => Uint8Array = defaultRandom): Promise<string> {
  const existing = await store.getItem(KEY);
  if (existing && /^[0-9a-f-]{36}$/.test(existing)) return existing;
  const id = randomUuid(random);
  await store.setItem(KEY, id);
  return id;
}
