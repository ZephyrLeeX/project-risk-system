import { Injectable } from "@nestjs/common";
import ExcelJS from "exceljs";
import { simpleParser } from "mailparser";
import * as mammoth from "mammoth";
import { PDFParse } from "pdf-parse";

import type {
  MailAttachmentItem,
  MailProcessingTraceItem,
} from "@risk-platform/contracts";

const MAX_SOURCE_BYTES = 25 * 1024 * 1024;
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const MAX_EXTRACTED_CHARS = 60_000;

export interface ParsedMailContent {
  subject: string;
  senderName: string | null;
  senderAddress: string | null;
  sentAt: Date | null;
  messageId: string;
  text: string;
  keyPoints: string[];
  attachments: MailAttachmentItem[];
  traces: MailProcessingTraceItem[];
  hasAttachmentFailure: boolean;
}

@Injectable()
export class MailContentParserService {
  async parse(source: Buffer, fallbackMessageId: string): Promise<ParsedMailContent> {
    if (source.length > MAX_SOURCE_BYTES) {
      throw new Error("邮件大小超过25MB安全处理上限");
    }
    const startedAt = new Date();
    const parsed = await simpleParser(source, {
      skipHtmlToText: false,
      skipTextToHtml: true,
      maxHtmlLengthToParse: 2 * 1024 * 1024,
    });
    const attachmentResults: MailAttachmentItem[] = [];
    const attachmentTexts: string[] = [];
    for (const attachment of parsed.attachments) {
      const result = await this.parseAttachment(
        attachment.filename || "未命名附件",
        attachment.contentType,
        attachment.size,
        attachment.content,
      );
      attachmentResults.push(result.metadata);
      if (result.text) attachmentTexts.push(result.text);
    }
    const plain = this.cleanText(parsed.text || "");
    const combined = this.cleanText([plain, ...attachmentTexts].filter(Boolean).join("\n\n"));
    const traces: MailProcessingTraceItem[] = [
      this.trace("邮件读取与去重校验", "COMPLETED", "已读取邮件源文件并完成Message-ID与UID校验", startedAt),
      this.trace(
        "正文清洗与附件解析",
        attachmentResults.some((item) => item.status === "FAILED") ? "FAILED" : "COMPLETED",
        `正文已安全清洗，附件${attachmentResults.length}个`,
        new Date(),
      ),
    ];
    const from = parsed.from?.value[0];
    return {
      subject: (parsed.subject || "（无主题）").slice(0, 500),
      senderName: from?.name?.slice(0, 255) || null,
      senderAddress: from?.address?.toLocaleLowerCase().slice(0, 255) || null,
      sentAt: parsed.date || null,
      messageId: (parsed.messageId || fallbackMessageId).slice(0, 500),
      text: combined.slice(0, MAX_EXTRACTED_CHARS),
      keyPoints: this.keyPoints(combined),
      attachments: attachmentResults,
      traces,
      hasAttachmentFailure: attachmentResults.some((item) => item.status === "FAILED"),
    };
  }

  private async parseAttachment(
    name: string,
    contentType: string,
    size: number,
    content: Buffer,
  ): Promise<{ metadata: MailAttachmentItem; text: string }> {
    const extension = name.split(".").pop()?.toLocaleLowerCase() || "";
    const base = { name: name.slice(0, 255), type: extension.toUpperCase() || contentType, sizeBytes: size };
    if (size > MAX_ATTACHMENT_BYTES) {
      return { metadata: { ...base, status: "FAILED", summary: "附件超过10MB安全解析上限" }, text: "" };
    }
    if (!["txt", "docx", "pdf", "xlsx"].includes(extension)) {
      return { metadata: { ...base, status: "SKIPPED", summary: "非支持格式，仅保留安全元数据" }, text: "" };
    }
    try {
      let text = "";
      if (extension === "txt") text = content.toString("utf8");
      if (extension === "docx") text = (await mammoth.extractRawText({ buffer: Buffer.from(content) })).value;
      if (extension === "pdf") {
        const parser = new PDFParse({ data: new Uint8Array(content) });
        try {
          text = (await parser.getText()).text;
        } finally {
          await parser.destroy();
        }
      }
      if (extension === "xlsx") {
        const workbook = new ExcelJS.Workbook();
        await workbook.xlsx.load(Buffer.from(content) as unknown as ArrayBuffer);
        const rows: string[] = [];
        workbook.eachSheet((sheet) => {
          sheet.eachRow((row) => {
            const values = Array.isArray(row.values) ? row.values.slice(1) : [row.values];
            rows.push(values.map(String).join(" | "));
          });
        });
        text = rows.join("\n");
      }
      const cleaned = this.cleanText(text).slice(0, 20_000);
      return {
        metadata: { ...base, status: "PARSED", summary: cleaned ? cleaned.slice(0, 180) : "附件未提取到可读文本" },
        text: cleaned,
      };
    } catch {
      return { metadata: { ...base, status: "FAILED", summary: "附件解析失败，未执行附件中的任何内容" }, text: "" };
    }
  }

  private cleanText(value: string): string {
    return value
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/https?:\/\/\S+/gi, "[外部链接已移除]")
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, " ")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  private keyPoints(text: string): string[] {
    return text
      .split(/(?<=[。！？!?])|\n+/)
      .map((item) => item.trim())
      .filter((item) => item.length >= 8)
      .slice(0, 5)
      .map((item) => item.slice(0, 300));
  }

  private trace(
    stage: string,
    status: MailProcessingTraceItem["status"],
    detail: string,
    occurredAt: Date,
  ): MailProcessingTraceItem {
    return { stage, status, detail, occurredAt: occurredAt.toISOString() };
  }
}
