import { createPublicKey, randomBytes, verify } from "node:crypto";

import { fromBase64, SoftwareDeviceKey, toBase64 } from "../src";

const rng = (n: number) => new Uint8Array(randomBytes(n));

describe("device key is compatible with the server (OpenSSL, as Python cryptography uses)", () => {
  it("public key is DER SubjectPublicKeyInfo for P-256", () => {
    const key = SoftwareDeviceKey.generate(rng);
    const pub = createPublicKey({ key: Buffer.from(fromBase64(key.publicKeySpki())), format: "der", type: "spki" });
    expect(pub.asymmetricKeyType).toBe("ec");
    expect(pub.asymmetricKeyDetails?.namedCurve).toBe("prime256v1");
  });

  it("signature over the refresh token verifies with ECDSA SHA-256 (DER)", async () => {
    const key = SoftwareDeviceKey.generate(rng);
    const pub = createPublicKey({ key: Buffer.from(fromBase64(key.publicKeySpki())), format: "der", type: "spki" });
    for (const message of ["rt_0190abcd.secret-part", "٣٤٥ unicode ✓", ""]) {
      const sig = Buffer.from(fromBase64(await key.sign(message)));
      expect(verify("sha256", Buffer.from(message, "utf8"), pub, sig)).toBe(true);
      expect(verify("sha256", Buffer.from(message + "x", "utf8"), pub, sig)).toBe(false);
    }
  });

  it("exported key restores the same identity", async () => {
    const key = SoftwareDeviceKey.generate(rng);
    const again = SoftwareDeviceKey.fromExport(key.export());
    expect(again.publicKeySpki()).toBe(key.publicKeySpki());
    expect(() => SoftwareDeviceKey.fromExport("AAAA")).toThrow();
  });

  it("base64 matches Node for every length", () => {
    for (let n = 0; n < 70; n += 1) {
      const bytes = rng(n);
      expect(toBase64(bytes)).toBe(Buffer.from(bytes).toString("base64"));
      expect(Buffer.from(fromBase64(toBase64(bytes))).equals(Buffer.from(bytes))).toBe(true);
    }
    expect(() => fromBase64("a$b")).toThrow();
  });
});
