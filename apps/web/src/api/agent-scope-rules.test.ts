import { afterEach, describe, expect, it, vi } from "vitest";

import { agentScopeRulesApi } from "./agent-scope-rules";

function apiResponse(data: unknown, status = 200): Response {
  return new Response(
    JSON.stringify({ code: "OK", message: "success", data, traceId: "trace-id" }),
    { status },
  );
}

function errorResponse(status: number, code: string, message: string): Response {
  return new Response(JSON.stringify({ code, message, data: null, traceId: "trace-id" }), {
    status,
  });
}

function rule(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    name: "全部项目拦截",
    decision: "BLOCK",
    matchType: "PHRASE",
    pattern: "全部项目",
    priority: 10,
    enabled: false,
    description: null,
    version: 3,
    createdBy: "admin",
    createdAt: "2026-08-20T10:00:00.000Z",
    updatedAt: "2026-08-20T10:00:00.000Z",
    warnings: [{ code: "BROAD_BLOCK_RULE", message: "该拦截规则范围较宽，可能影响正常查询" }],
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("agent scope rules API client", () => {
  it("lists rules from the admin scope-rules endpoint", async () => {
    const fetch = vi.fn().mockResolvedValue(apiResponse([rule()]));
    vi.stubGlobal("fetch", fetch);

    const data = await agentScopeRulesApi.list();

    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/agent/scope-rules",
      expect.any(Object),
    );
    expect(data).toHaveLength(1);
    expect(data[0]?.warnings?.[0]?.code).toBe("BROAD_BLOCK_RULE");
  });

  it("creates a rule defaulting to disabled", async () => {
    const fetch = vi.fn().mockResolvedValue(apiResponse(rule({ enabled: false })));
    vi.stubGlobal("fetch", fetch);

    await agentScopeRulesApi.create({
      name: "全部项目拦截",
      decision: "BLOCK",
      matchType: "PHRASE",
      pattern: "全部项目",
      priority: 10,
      enabled: false,
      description: null,
    });

    const [path, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/admin/agent/scope-rules");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toMatchObject({
      name: "全部项目拦截",
      enabled: false,
    });
  });

  it("sends the optimistic-lock version on update", async () => {
    const fetch = vi.fn().mockResolvedValue(
      apiResponse(rule({ version: 4, enabled: true })),
    );
    vi.stubGlobal("fetch", fetch);

    await agentScopeRulesApi.update(rule().id as string, { version: 3, enabled: true });

    const [path, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe(`/api/admin/agent/scope-rules/${rule().id}`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ version: 3, enabled: true });
  });

  it("surfaces the 409 conflict code so the UI can prompt a reload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(errorResponse(409, "CONFLICT", "规则已被他人修改，请刷新后重试")),
    );

    const error = await agentScopeRulesApi
      .update(rule().id as string, { version: 1, enabled: true })
      .catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(Error);
    expect((error as { status: number }).status).toBe(409);
    expect((error as { code: string }).code).toBe("CONFLICT");
  });

  it("carries the delete version as a query param", async () => {
    const fetch = vi.fn().mockResolvedValue(apiResponse(null));
    vi.stubGlobal("fetch", fetch);

    await agentScopeRulesApi.remove(rule().id as string, 7);

    const [path, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe(`/api/admin/agent/scope-rules/${rule().id}?version=7`);
    expect(init.method).toBe("DELETE");
  });

  it("previews an unsaved candidate through the server-side test endpoint", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        apiResponse({
          decision: "BLOCK",
          source: "RUNTIME_RULE",
          matchedRule: { id: "", name: "(预览规则)", matchType: "PHRASE", decision: "BLOCK", priority: 10 },
          preview: true,
          previewRuleId: null,
          warnings: [],
        }),
      );
    vi.stubGlobal("fetch", fetch);

    const result = await agentScopeRulesApi.test({
      message: "帮我查全部项目",
      candidateRule: { decision: "BLOCK", matchType: "PHRASE", pattern: "全部项目", priority: 10 },
    });

    const [path, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/admin/agent/scope-rules/test");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toMatchObject({
      message: "帮我查全部项目",
      candidateRule: { pattern: "全部项目" },
    });
    expect(result.preview).toBe(true);
    expect(result.matchedRule?.id).toBe("");
  });

  it("previews one saved rule by id without a candidate", async () => {
    const fetch = vi.fn().mockResolvedValue(
      apiResponse({ decision: "BLOCK", source: "RUNTIME_RULE", matchedRule: null, preview: true, previewRuleId: rule().id, warnings: [] }),
    );
    vi.stubGlobal("fetch", fetch);

    await agentScopeRulesApi.test({ message: "帮我查全部项目", ruleId: rule().id as string });

    expect(JSON.parse((fetch.mock.calls[0] as [string, RequestInit])[1]!.body as string)).toEqual({
      message: "帮我查全部项目",
      ruleId: rule().id,
    });
  });
});
