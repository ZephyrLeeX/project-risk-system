import { Injectable, InternalServerErrorException } from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import {
  createCipheriv,
  createDecipheriv,
  randomBytes,
} from "node:crypto";

export interface EncryptedCredential {
  ciphertext: string;
  iv: string;
  authTag: string;
  last4: string;
}

@Injectable()
export class CredentialEncryptionService {
  private readonly key: Buffer;

  constructor(config: ConfigService) {
    const encodedKey = config.get<string>("DATA_ENCRYPTION_KEY", "");
    const key = Buffer.from(encodedKey, "base64");
    if (key.length !== 32) {
      throw new InternalServerErrorException(
        "DATA_ENCRYPTION_KEY 必须是32字节Base64密钥",
      );
    }
    this.key = key;
  }

  encrypt(value: string): EncryptedCredential {
    const iv = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.key, iv);
    const ciphertext = Buffer.concat([
      cipher.update(value, "utf8"),
      cipher.final(),
    ]);
    return {
      ciphertext: ciphertext.toString("base64"),
      iv: iv.toString("base64"),
      authTag: cipher.getAuthTag().toString("base64"),
      last4: value.slice(-4),
    };
  }

  decrypt(value: {
    encryptedApiKey: string;
    keyIv: string;
    keyAuthTag: string;
  }): string {
    return this.decryptCredential(
      {
        ciphertext: value.encryptedApiKey,
        iv: value.keyIv,
        authTag: value.keyAuthTag,
      },
      "AI服务凭据解密失败",
    );
  }

  decryptCredential(
    value: { ciphertext: string; iv: string; authTag: string },
    failureMessage = "加密凭据解密失败",
  ): string {
    try {
      const decipher = createDecipheriv(
        "aes-256-gcm",
        this.key,
        Buffer.from(value.iv, "base64"),
      );
      decipher.setAuthTag(Buffer.from(value.authTag, "base64"));
      return Buffer.concat([
        decipher.update(Buffer.from(value.ciphertext, "base64")),
        decipher.final(),
      ]).toString("utf8");
    } catch {
      throw new InternalServerErrorException(failureMessage);
    }
  }
}
