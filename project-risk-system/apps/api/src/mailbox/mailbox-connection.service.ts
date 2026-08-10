import { Injectable } from "@nestjs/common";
import { isIP } from "node:net";
import { ImapFlow } from "imapflow";

import { resolvePublicMailboxHost } from "./mailbox-policy";

export interface MailboxConnectionRequest {
  email: string;
  authCode: string;
  host: string;
  port: number;
  encryption: "SSL" | "STARTTLS";
  folder: string;
}

export interface MailboxConnectionOutcome {
  success: boolean;
  latencyMs: number;
  errorCode: string | null;
  errorSummary: string | null;
}

@Injectable()
export class MailboxConnectionService {
  async connect(request: MailboxConnectionRequest): Promise<ImapFlow> {
    const resolvedHost = await resolvePublicMailboxHost(request.host);
    const client = this.createClient(request, resolvedHost);
    await client.connect();
    return client;
  }

  async test(request: MailboxConnectionRequest): Promise<MailboxConnectionOutcome> {
    const startedAt = Date.now();
    let client: ImapFlow | null = null;
    try {
      client = await this.connect(request);
      await client.mailboxOpen(request.folder, { readOnly: true });
      await client.logout();
      return {
        success: true,
        latencyMs: Date.now() - startedAt,
        errorCode: null,
        errorSummary: null,
      };
    } catch (error) {
      client?.close();
      const classified = this.classify(error);
      return {
        success: false,
        latencyMs: Date.now() - startedAt,
        ...classified,
      };
    }
  }

  classify(error: unknown): { errorCode: string; errorSummary: string } {
    const message = error instanceof Error ? error.message : String(error);
    const code =
      error && typeof error === "object" && "code" in error
        ? String((error as { code?: unknown }).code ?? "")
        : "";
    if (/auth|login|credential|password/i.test(`${code} ${message}`)) {
      return { errorCode: "AUTHENTICATION_FAILED", errorSummary: "邮箱地址或授权码验证失败" };
    }
    if (/mailbox|folder|not found|nonexistent/i.test(message)) {
      return { errorCode: "FOLDER_NOT_FOUND", errorSummary: "无法访问所选邮件文件夹" };
    }
    if (/timeout|timed out/i.test(`${code} ${message}`)) {
      return { errorCode: "CONNECTION_TIMEOUT", errorSummary: "连接IMAP服务器超时" };
    }
    if (/certificate|tls|ssl/i.test(`${code} ${message}`)) {
      return { errorCode: "TLS_VERIFICATION_FAILED", errorSummary: "IMAP服务器TLS证书校验失败" };
    }
    return { errorCode: "IMAP_UNREACHABLE", errorSummary: "无法连接IMAP服务器或服务器拒绝访问" };
  }

  private createClient(request: MailboxConnectionRequest, resolvedHost: string): ImapFlow {
    const client = new ImapFlow({
      host: resolvedHost,
      servername: isIP(request.host) ? undefined : request.host,
      port: request.port,
      secure: request.encryption === "SSL",
      doSTARTTLS: request.encryption === "STARTTLS",
      auth: { user: request.email, pass: request.authCode },
      logger: false,
      disableAutoIdle: true,
      disableCompression: true,
      connectionTimeout: 10_000,
      greetingTimeout: 8_000,
      socketTimeout: 12_000,
      maxLineLength: 1_048_576,
      maxLiteralSize: 26_214_400,
      tls: {
        minVersion: "TLSv1.2",
        rejectUnauthorized: true,
        servername: isIP(request.host) ? undefined : request.host,
      },
    });
    client.on("error", () => undefined);
    return client;
  }
}
