import { BadRequestException, Injectable } from "@nestjs/common";
import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

export interface AiConnectionRequest {
  endpoint: string;
  model: string;
  apiKey: string;
  timeoutSeconds: number;
  retryCount: number;
}

export interface AiConnectionOutcome {
  success: boolean;
  latencyMs: number;
  errorCode: string | null;
  errorSummary: string | null;
}

@Injectable()
export class AiConnectionService {
  async test(request: AiConnectionRequest): Promise<AiConnectionOutcome> {
    const modelsUrl = await this.modelsUrl(request.endpoint);
    const startedAt = Date.now();
    let lastErrorCode = "CONNECTION_FAILED";
    let lastErrorSummary = "AI服务连接失败";

    for (let attempt = 0; attempt <= request.retryCount; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(
        () => controller.abort(),
        request.timeoutSeconds * 1_000,
      );
      try {
        const response = await fetch(modelsUrl, {
          method: "GET",
          headers: {
            authorization: `Bearer ${request.apiKey}`,
            accept: "application/json",
          },
          signal: controller.signal,
          redirect: "error",
        });
        if (response.ok) {
          return {
            success: true,
            latencyMs: Date.now() - startedAt,
            errorCode: null,
            errorSummary: null,
          };
        }
        lastErrorCode = `HTTP_${response.status}`;
        lastErrorSummary = `上游服务返回 HTTP ${response.status}`;
      } catch (error) {
        const aborted =
          error instanceof Error && error.name === "AbortError";
        lastErrorCode = aborted ? "UPSTREAM_TIMEOUT" : "UPSTREAM_UNREACHABLE";
        lastErrorSummary = aborted
          ? `连接测试超过${request.timeoutSeconds}秒`
          : "无法连接AI服务地址";
      } finally {
        clearTimeout(timer);
      }
    }

    return {
      success: false,
      latencyMs: Date.now() - startedAt,
      errorCode: lastErrorCode,
      errorSummary: lastErrorSummary,
    };
  }

  private async modelsUrl(endpoint: string): Promise<string> {
    let url: URL;
    try {
      url = new URL(endpoint);
    } catch {
      throw new BadRequestException("AI服务地址格式不正确");
    }
    if (url.protocol !== "https:") {
      throw new BadRequestException("AI服务地址必须使用HTTPS");
    }
    if (url.username || url.password) {
      throw new BadRequestException("AI服务地址不能包含账号或密码");
    }
    await this.assertPublicHost(url.hostname);
    url.pathname = `${url.pathname.replace(/\/$/, "")}/models`;
    url.search = "";
    url.hash = "";
    return url.toString();
  }

  private async assertPublicHost(hostname: string): Promise<void> {
    const rawHostname = hostname.toLowerCase();
    const normalized =
      rawHostname.startsWith("[") && rawHostname.endsWith("]")
        ? rawHostname.slice(1, -1)
        : rawHostname;
    if (
      normalized === "localhost" ||
      normalized.endsWith(".localhost") ||
      normalized.endsWith(".local")
    ) {
      throw new BadRequestException("AI服务地址不能指向本机或内网");
    }
    let addresses: Array<{ address: string; family: number }>;
    try {
      addresses = isIP(normalized)
        ? [{ address: normalized, family: isIP(normalized) }]
        : await lookup(normalized, { all: true });
    } catch {
      throw new BadRequestException("无法解析AI服务地址");
    }
    if (
      !addresses.length ||
      addresses.some(({ address }) => this.isPrivateAddress(address))
    ) {
      throw new BadRequestException("AI服务地址不能指向本机或内网");
    }
  }

  private isPrivateAddress(address: string): boolean {
    const value = address.toLowerCase();
    if (value.includes(":")) {
      return (
        value === "::1" ||
        value.startsWith("fc") ||
        value.startsWith("fd") ||
        value.startsWith("fe8") ||
        value.startsWith("fe9") ||
        value.startsWith("fea") ||
        value.startsWith("feb") ||
        value.startsWith("::ffff:127.") ||
        value.startsWith("::ffff:10.") ||
        value.startsWith("::ffff:192.168.")
      );
    }
    const octets = value.split(".").map(Number);
    if (octets.length !== 4 || octets.some((item) => Number.isNaN(item))) {
      return true;
    }
    const [first, second] = octets;
    if (first === undefined) {
      return true;
    }
    return (
      first === 0 ||
      first === 10 ||
      first === 127 ||
      (first === 169 && second === 254) ||
      (first === 172 && second !== undefined && second >= 16 && second <= 31) ||
      (first === 192 && second === 168) ||
      first >= 224
    );
  }
}
