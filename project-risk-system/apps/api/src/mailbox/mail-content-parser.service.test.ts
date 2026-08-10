import { describe, expect, it } from "vitest";

import { MailContentParserService } from "./mail-content-parser.service";

describe("MailContentParserService", () => {
  it("parses a real RFC822 message and removes external links from extracted text", async () => {
    const source = Buffer.from([
      "From: Risk Reporter <Risk.Reporter@Example.com>",
      "To: receiver@example.com",
      "Subject: =?UTF-8?B?6aG555uu5ZGo5oql?=",
      "Message-ID: <weekly-001@example.com>",
      "Date: Thu, 23 Jul 2026 09:18:00 +0800",
      "Content-Type: text/plain; charset=utf-8",
      "",
      "项目本周存在回款延期风险。详情见 https://unsafe.example/path",
      "建议按周跟踪付款节点。",
    ].join("\r\n"), "utf8");

    const parsed = await new MailContentParserService().parse(source, "fallback-id");

    expect(parsed.subject).toBe("项目周报");
    expect(parsed.senderAddress).toBe("risk.reporter@example.com");
    expect(parsed.messageId).toBe("<weekly-001@example.com>");
    expect(parsed.text).toContain("[外部链接已移除]");
    expect(parsed.text).not.toContain("unsafe.example");
    expect(parsed.keyPoints.length).toBeGreaterThan(0);
    expect(parsed.hasAttachmentFailure).toBe(false);
  });

  it("rejects an oversized mail source before parsing", async () => {
    const oversized = Buffer.alloc(25 * 1024 * 1024 + 1);
    await expect(new MailContentParserService().parse(oversized, "fallback-id"))
      .rejects.toThrow("邮件大小超过25MB安全处理上限");
  });
});
