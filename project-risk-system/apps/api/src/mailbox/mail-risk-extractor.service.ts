import { Injectable } from "@nestjs/common";
import {
  AiCallResult,
  AiCallScene,
  AiConnectionStatus,
  ProjectRiskLevel,
} from "@prisma/client";
import { randomUUID } from "node:crypto";

import { CredentialEncryptionService } from "../ai-providers/credential-encryption.service";
import { PrismaService } from "../prisma/prisma.service";
import type { ProjectMatchResult } from "./mail-project-matcher.service";

export interface ExtractedMailRisk {
  projectId: string;
  categoryId: string;
  level: ProjectRiskLevel;
  description: string;
  evidence: string;
  suggestion: string;
  confidence: number;
}

export interface MailRiskExtractionResult {
  keyPoints: string[];
  risks: ExtractedMailRisk[];
}

@Injectable()
export class MailRiskExtractorService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly encryption: CredentialEncryptionService,
  ) {}

  async extract(input: {
    subject: string;
    text: string;
    matches: ProjectMatchResult[];
    actorUserId: string;
  }): Promise<MailRiskExtractionResult> {
    const provider = await this.prisma.aiProviderConfig.findFirst({
      where: { enabled: true, lastTestStatus: AiConnectionStatus.HEALTHY },
      orderBy: [{ isDefault: "desc" }, { priority: "asc" }],
    });
    if (!provider) throw this.safeError("AI_PROVIDER_UNAVAILABLE", "没有可用的AI服务，请先在后台完成API Key连接测试");
    const categories = await this.prisma.riskCategory.findMany({
      where: { isActive: true },
      select: { id: true, name: true, description: true },
      orderBy: { sortOrder: "asc" },
    });
    const apiKey = this.encryption.decrypt(provider);
    const traceId = randomUUID();
    const started = Date.now();
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), provider.timeoutSeconds * 1_000);
      let response: Response;
      try {
        response = await fetch(this.completionsUrl(provider.endpoint), {
          method: "POST",
          headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json", accept: "application/json" },
          body: JSON.stringify({
            model: provider.model,
            temperature: 0.1,
            response_format: { type: "json_object" },
            messages: [
              { role: "system", content: "你是项目风险分析器。只根据给定周报提取事实，不得编造。输出JSON对象：keyPoints为最多5条字符串；risks为数组，每项包含projectId、categoryId、level(HIGH/MEDIUM/LOW)、description、evidence、suggestion、confidence(0-100)。没有风险时risks为空。" },
              { role: "user", content: JSON.stringify({ subject: input.subject, projects: input.matches.map(({ projectId, projectName }) => ({ projectId, projectName })), categories, content: input.text.slice(0, 24_000) }) },
            ],
          }),
          signal: controller.signal,
          redirect: "error",
        });
      } finally {
        clearTimeout(timer);
      }
      if (!response.ok) throw this.safeError(`HTTP_${response.status}`, `AI服务返回HTTP ${response.status}`);
      const body = await response.json() as {
        choices?: Array<{ message?: { content?: string } }>;
        usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
      };
      const parsed = this.parse(body.choices?.[0]?.message?.content || "", input.matches, categories);
      await this.recordCall({ traceId, provider, actorUserId: input.actorUserId, durationMs: Date.now() - started, result: AiCallResult.SUCCESS, usage: body.usage });
      return parsed;
    } catch (error) {
      const safe = this.asSafeError(error);
      await this.recordCall({ traceId, provider, actorUserId: input.actorUserId, durationMs: Date.now() - started, result: AiCallResult.FAILURE, errorCode: safe.code, errorSummary: safe.message });
      throw safe;
    }
  }

  private parse(
    value: string,
    matches: ProjectMatchResult[],
    categories: Array<{ id: string; name: string; description: string | null }>,
  ): MailRiskExtractionResult {
    let raw: unknown;
    try {
      raw = JSON.parse(value.replace(/^```(?:json)?/i, "").replace(/```$/i, "").trim());
    } catch {
      throw this.safeError("AI_RESPONSE_INVALID", "AI服务未返回有效的结构化风险结果");
    }
    const record = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
    const projectIds = new Set(matches.map((item) => item.projectId));
    const categoryIds = new Set(categories.map((item) => item.id));
    const keyPoints = Array.isArray(record.keyPoints)
      ? record.keyPoints.filter((item): item is string => typeof item === "string").map((item) => item.trim().slice(0, 300)).filter(Boolean).slice(0, 5)
      : [];
    const risks: ExtractedMailRisk[] = [];
    for (const item of Array.isArray(record.risks) ? record.risks : []) {
      if (!item || typeof item !== "object") continue;
      const risk = item as Record<string, unknown>;
      const projectId = String(risk.projectId || "");
      const categoryId = String(risk.categoryId || "");
      const level = String(risk.level || "") as ProjectRiskLevel;
      if (!projectIds.has(projectId) || !categoryIds.has(categoryId) || !Object.values(ProjectRiskLevel).includes(level)) continue;
      const description = String(risk.description || "").trim();
      const evidence = String(risk.evidence || "").trim();
      const suggestion = String(risk.suggestion || "").trim();
      const confidence = Math.max(0, Math.min(100, Math.round(Number(risk.confidence) || 0)));
      if (description.length < 4 || evidence.length < 2 || suggestion.length < 2) continue;
      risks.push({ projectId, categoryId, level, description: description.slice(0, 4000), evidence: evidence.slice(0, 4000), suggestion: suggestion.slice(0, 4000), confidence });
    }
    return { keyPoints, risks: risks.slice(0, 30) };
  }

  private completionsUrl(endpoint: string): string {
    const url = new URL(endpoint);
    url.pathname = `${url.pathname.replace(/\/$/, "")}/chat/completions`;
    url.search = "";
    url.hash = "";
    return url.toString();
  }

  private async recordCall(input: {
    traceId: string;
    provider: { id: string; name: string; model: string };
    actorUserId: string;
    durationMs: number;
    result: AiCallResult;
    usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
    errorCode?: string;
    errorSummary?: string;
  }): Promise<void> {
    await this.prisma.aiCallLog.create({ data: {
      traceId: input.traceId,
      providerId: input.provider.id,
      providerNameSnapshot: input.provider.name,
      modelSnapshot: input.provider.model,
      scene: AiCallScene.RISK_EXTRACTION,
      inputTokens: input.usage?.prompt_tokens ?? 0,
      outputTokens: input.usage?.completion_tokens ?? 0,
      totalTokens: input.usage?.total_tokens ?? 0,
      durationMs: input.durationMs,
      result: input.result,
      errorCode: input.errorCode,
      errorSummary: input.errorSummary,
      actorUserId: input.actorUserId,
    } });
  }

  private safeError(code: string, message: string): Error & { code: string } {
    return Object.assign(new Error(message), { code });
  }

  private asSafeError(error: unknown): Error & { code: string } {
    if (error instanceof Error && "code" in error) return error as Error & { code: string };
    if (error instanceof Error && error.name === "AbortError") return this.safeError("AI_TIMEOUT", "AI风险提取超时");
    return this.safeError("AI_EXTRACTION_FAILED", "AI风险提取失败，请稍后重试");
  }
}
