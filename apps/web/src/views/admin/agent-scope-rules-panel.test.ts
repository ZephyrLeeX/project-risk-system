import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const panel = readFileSync(
  new URL("../../components/admin/AgentScopeRulesPanel.vue", import.meta.url),
  "utf8",
);
const systemConfigView = readFileSync(
  new URL("./SystemConfigView.vue", import.meta.url),
  "utf8",
);

describe("agent scope rules panel wiring", () => {
  it("is gated by agent.scope.manage in the directory with the backend still authoritative", () => {
    // The directory entry appears only for holders of agent.scope.manage.
    expect(systemConfigView).toContain(
      'auth.user?.permissions.includes("agent.scope.manage")',
    );
    expect(systemConfigView).toContain('name: "Agent 范围规则"');
    // The section itself re-checks the permission before rendering the panel.
    expect(systemConfigView).toContain(
      "activeSection==='agentScope' && auth.user?.permissions.includes('agent.scope.manage')",
    );
    // The panel itself claims no frontend authority — mutations rely on the
    // API's 403 rather than client-side permission logic.
    expect(panel).not.toContain("permissions.includes");
  });

  it("sits between 项目别名 and 会话与登录 in the directory order", () => {
    const aliasIndex = systemConfigView.indexOf('name: "项目别名"');
    const scopeIndex = systemConfigView.indexOf('name: "Agent 范围规则"');
    const securityIndex = systemConfigView.indexOf('name: "会话与登录"');
    expect(aliasIndex).toBeGreaterThan(-1);
    expect(scopeIndex).toBeGreaterThan(aliasIndex);
    expect(securityIndex).toBeGreaterThan(scopeIndex);
  });

  it("stays out of the SystemConfig snapshot and publish flow", () => {
    // The panel never marks the AGENT module dirty nor touches publish state.
    expect(panel).not.toContain("markChanged");
    expect(panel).not.toContain("systemConfigApi");
    expect(panel).not.toContain("changedModules");
    expect(systemConfigView).not.toContain('markChanged("AGENT_SCOPE")');
  });
});

describe("agent scope rules panel CRUD and locking", () => {
  it("creates with enabled defaulting to false", () => {
    expect(panel).toContain("enabled: false, description: \"\"");
    expect(panel).toContain("规则已创建（默认停用，请先用测试接口验证）");
  });

  it("carries the version optimistic-lock token on update, toggle and delete", () => {
    expect(panel).toContain("version: editingVersion.value,");
    expect(panel).toContain("version: rule.version,\n      enabled: !rule.enabled,");
    expect(panel).toContain("agentScopeRulesApi.remove(target.id, target.version)");
  });

  it("maps a 409 to the reload prompt and refreshes the list", () => {
    expect(panel).toContain("function isConflict(reason: unknown): boolean");
    expect(panel).toContain("CONFLICT_MESSAGE");
    expect(panel).toContain("该规则已被其他管理员修改，请刷新后重试");
    // Conflict paths reload the list so the stale version token is replaced.
    expect(panel.match(/isConflict\(reason\)/g)?.length).toBeGreaterThanOrEqual(3);
  });

  it("keeps the toggle row disabled in flight and reverts via reload on failure", () => {
    expect(panel).toContain("const togglingId = ref<string | null>(null)");
    expect(panel).toContain(':disabled="togglingId !== null"');
    // On failure the row is refreshed from the server (never a local guess).
    expect(panel).toContain("await load();");
  });

  it("validates the editor fields before any request", () => {
    expect(panel).toContain("function validateForm(): string");
    expect(panel).toContain("规则名称长度需为 2-100 个字符");
    expect(panel).toContain("匹配模式不能为空");
    expect(panel).toContain("优先级需为 0-1000 的整数");
  });

  it("deletes behind a confirmation modal with a danger action", () => {
    expect(panel).toContain('title="删除范围规则？"');
    expect(panel).toContain("admin-danger-button");
    expect(panel).toContain("confirmDelete");
  });
});

describe("agent scope rules panel labels, warnings and preview", () => {
  it("uses Chinese labels with the raw enum visible for decision and match", () => {
    expect(panel).toContain('"允许（ALLOW）"');
    expect(panel).toContain('"拦截（BLOCK）"');
    expect(panel).toContain('"精确匹配（EXACT）"');
    expect(panel).toContain('"短语匹配（PHRASE）"');
    expect(panel).toContain("EXACT：整条消息完全一致才命中");
    expect(panel).toContain("PHRASE：消息包含该短语即命中");
  });

  it("styles ALLOW as success and BLOCK as danger, never color-only", () => {
    expect(panel).toContain("is-allow");
    expect(panel).toContain("is-block");
    expect(panel).toContain("{{ rule.decision === \"ALLOW\" ? \"允许\" : \"拦截\" }}");
  });

  it("displays warnings prominently but never blocks the save", () => {
    // Warnings render in list rows, test results and the editor preview…
    expect(panel.match(/scope-rule-warnings/g)?.length).toBeGreaterThanOrEqual(3);
    // …and the save path only fails on validation/transport, never on warnings.
    expect(panel).not.toContain("warnings.length > 0");
    expect(panel).not.toContain("BROAD_BLOCK_RULE\")\n      return");
  });

  it("previews the unsaved draft server-side via candidateRule", () => {
    expect(panel).toContain("测试当前草稿");
    expect(panel).toContain("candidateRule: {");
    expect(panel).toContain("未保存的候选规则");
    expect(panel).toContain("草稿预览不会写入数据库");
    // No frontend simulation of Layer-1 matching exists anywhere in the panel.
    expect(panel).not.toContain("pattern.includes(");
    expect(panel).not.toContain("message.includes(");
    expect(panel).not.toContain(".toLowerCase() ===");
  });

  it("shows the full test result: decision, source, matched rule, preview, warnings", () => {
    expect(panel).toContain("{{ testResult.decision }}");
    expect(panel).toContain("{{ sourceLabel[testResult.source] }}");
    expect(panel).toContain("testResult.matchedRule");
    expect(panel).toContain("testResult.preview");
    expect(panel).toContain("testResult.previewRuleId");
    expect(panel).toContain("testResult.warnings");
  });

  it("documents the server-side sort key and the 15s propagation caveat", () => {
    expect(panel).toContain("_rule_sort_key");
    expect(panel).toContain("优先级降序 → 精确匹配（EXACT）优先于短语匹配（PHRASE）");
    expect(panel).toContain("拦截（BLOCK）优先于允许（ALLOW）");
    expect(panel).toContain("最长约 15 秒");
    // No instant-propagation (0ms) claims.
    expect(panel).not.toContain("立即全量生效");
    expect(panel).not.toContain("0 秒");
  });
});
