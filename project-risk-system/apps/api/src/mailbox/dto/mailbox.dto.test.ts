import "reflect-metadata";
import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { describe, expect, it } from "vitest";

import { MailboxConfigDto } from "./mailbox.dto";

const valid = {
  provider: "QQ",
  email: "risk@example.com",
  authCode: "mail-auth-code",
  imapHost: "imap.qq.com",
  imapPort: 993,
  encryption: "SSL",
  folder: "INBOX",
  subjectKeywords: ["项目周报", "风险周报"],
  senderRule: "@example.com",
  initialSyncWeeks: 4,
  readAttachments: true,
  aiExtractionEnabled: true,
};

describe("MailboxConfigDto", () => {
  it("accepts the approved personal mailbox form", async () => {
    expect(await validate(plainToInstance(MailboxConfigDto, valid))).toHaveLength(0);
  });

  it("rejects invalid ports, empty keywords and control characters in folders", async () => {
    const errors = await validate(
      plainToInstance(MailboxConfigDto, {
        ...valid,
        imapPort: 70000,
        folder: "INBOX\nBAD",
        subjectKeywords: [],
      }),
    );
    expect(errors.map((item) => item.property)).toEqual(
      expect.arrayContaining(["imapPort", "folder", "subjectKeywords"]),
    );
  });
});
