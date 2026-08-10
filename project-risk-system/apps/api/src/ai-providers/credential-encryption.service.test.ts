import { ConfigService } from "@nestjs/config";
import { describe, expect, it } from "vitest";

import { CredentialEncryptionService } from "./credential-encryption.service";

function service(): CredentialEncryptionService {
  return new CredentialEncryptionService(
    new ConfigService({ DATA_ENCRYPTION_KEY: Buffer.alloc(32, 7).toString("base64") }),
  );
}

describe("CredentialEncryptionService", () => {
  it("encrypts API keys with AES-GCM and decrypts them losslessly", () => {
    const encryption = service();
    const plaintext = "sk-test-secret-8D2F";
    const encrypted = encryption.encrypt(plaintext);

    expect(encrypted.ciphertext).not.toContain(plaintext);
    expect(encrypted.last4).toBe("8D2F");
    expect(encryption.decrypt({
      encryptedApiKey: encrypted.ciphertext,
      keyIv: encrypted.iv,
      keyAuthTag: encrypted.authTag,
    })).toBe(plaintext);
  });

  it("uses a fresh IV for every encryption", () => {
    const encryption = service();
    const first = encryption.encrypt("sk-test-secret-8D2F");
    const second = encryption.encrypt("sk-test-secret-8D2F");

    expect(first.iv).not.toBe(second.iv);
    expect(first.ciphertext).not.toBe(second.ciphertext);
  });

  it("rejects invalid encryption keys", () => {
    expect(() => new CredentialEncryptionService(new ConfigService({ DATA_ENCRYPTION_KEY: "invalid" }))).toThrow("DATA_ENCRYPTION_KEY");
  });
});
