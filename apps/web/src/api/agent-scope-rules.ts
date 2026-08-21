import type { OpenApi } from "@risk-platform/contracts";

import { apiRequest } from "./http";

/**
 * Generated OpenAPI Agent scope-rule contract types (Layer-1 runtime rules).
 * These aliases only re-reference the frozen generated authority — they are
 * never hand-written substitutes for the backend schemas.
 */
export type ScopeRule = OpenApi.components["schemas"]["ScopeRuleResponse"];
export type ScopeRuleWarning = OpenApi.components["schemas"]["ScopeRuleWarning"];
export type CreateScopeRuleRequest =
  OpenApi.components["schemas"]["CreateScopeRuleRequest"];
export type UpdateScopeRuleRequest =
  OpenApi.components["schemas"]["UpdateScopeRuleRequest"];
export type ScopeRuleTestRequest =
  OpenApi.components["schemas"]["ScopeRuleTestRequest"];
export type ScopeRuleTestResponse =
  OpenApi.components["schemas"]["ScopeRuleTestResponse"];

export const agentScopeRulesApi = {
  /** All rules, newest first (`GET /admin/agent/scope-rules`). */
  async list(): Promise<ScopeRule[]> {
    return (await apiRequest<ScopeRule[]>("/admin/agent/scope-rules")).data;
  },

  /**
   * Create a rule (`POST /admin/agent/scope-rules`).
   *
   * The backend forces new rules to start disabled; verify with `test` before
   * enabling so a mistaken live rule cannot immediately mis-block traffic.
   */
  async create(request: CreateScopeRuleRequest): Promise<ScopeRule> {
    return (
      await apiRequest<ScopeRule>("/admin/agent/scope-rules", {
        method: "POST",
        body: JSON.stringify(request),
      })
    ).data;
  },

  /**
   * Update a rule (`PATCH /admin/agent/scope-rules/{id}`).
   *
   * `version` is a required optimistic-lock token: a PATCH built from a stale
   * read answers 409 `CONFLICT` and must be retried after a reload.
   */
  async update(ruleId: string, request: UpdateScopeRuleRequest): Promise<ScopeRule> {
    return (
      await apiRequest<ScopeRule>(`/admin/agent/scope-rules/${encodeURIComponent(ruleId)}`, {
        method: "PATCH",
        body: JSON.stringify(request),
      })
    ).data;
  },

  /**
   * Soft-delete a rule (`DELETE /admin/agent/scope-rules/{id}?version=`).
   *
   * The version rides as a query param (DELETE carries no body) and is the
   * same optimistic-lock token as update — a stale delete answers 409.
   */
  async remove(ruleId: string, version: number): Promise<void> {
    await apiRequest<null>(
      `/admin/agent/scope-rules/${encodeURIComponent(ruleId)}?version=${version}`,
      { method: "DELETE" },
    );
  },

  /**
   * Evaluate a message against the live policy, optionally plus one saved
   * rule (preview) or one unsaved candidate draft (`POST /admin/agent/scope-rules/test`).
   *
   * `ruleId` and `candidateRule` are mutually exclusive; without either, the
   * message is evaluated against the live policy only. Matching always runs
   * server-side — the frontend never simulates Layer-1 evaluation.
   */
  async test(request: ScopeRuleTestRequest): Promise<ScopeRuleTestResponse> {
    return (
      await apiRequest<ScopeRuleTestResponse>("/admin/agent/scope-rules/test", {
        method: "POST",
        body: JSON.stringify(request),
      })
    ).data;
  },
};
