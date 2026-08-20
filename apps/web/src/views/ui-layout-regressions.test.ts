import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const dashboardView = readFileSync(new URL("./DashboardView.vue", import.meta.url), "utf8");
const mailboxSettingsView = readFileSync(
  new URL("./MailboxSettingsView.vue", import.meta.url),
  "utf8",
);
const systemConfigView = readFileSync(
  new URL("./admin/SystemConfigView.vue", import.meta.url),
  "utf8",
);
const adminShell = readFileSync(new URL("../components/AdminShell.vue", import.meta.url), "utf8");
const businessHeader = readFileSync(new URL("../components/BusinessHeader.vue", import.meta.url), "utf8");
const prototypePageStyles = readFileSync(
  new URL("../styles/prototype-pages.css", import.meta.url),
  "utf8",
);

describe("live UI layout regressions", () => {
  it("does not expose the internal Agent capability directory", () => {
    expect(dashboardView).not.toContain("可用能力：");
    expect(dashboardView).not.toContain("agentHelp.tools");
    expect(dashboardView).not.toContain("class=\"agent-tool-list\"");
  });

  it("keeps Agent suggestions content-sized instead of assigning them a viewport row", () => {
    expect(dashboardView).toContain("v-for=\"prompt in agentSuggestions\"");
    expect(prototypePageStyles).toContain(".agent-suggestions{display:grid");
    expect(prototypePageStyles).toContain("repeat(auto-fit,minmax(150px,1fr))");
    expect(prototypePageStyles).toContain(".agent-drawer{display:flex");
  });

  it("offers a disabled new-conversation control that resets locally", () => {
    expect(dashboardView).toContain('class="agent-header-actions"');
    expect(dashboardView).toContain("＋ 新建对话");
    expect(dashboardView).toContain(':disabled="agent.sending.value"');
    expect(dashboardView).toContain("function startNewAgentConversation(): void");
    expect(dashboardView).toContain("agent.reset();");
    expect(dashboardView).toContain('agentInput.value = "";');
    expect(dashboardView).toContain(
      "v-if=\"!agent.state.messages.length && agent.state.status === 'idle'\"",
    );
    expect(prototypePageStyles).toContain(".agent-header-actions{display:flex");
  });

  it("renders risk write confirmation as an editable modal initialized from the draft", () => {
    expect(dashboardView).toContain('title="确认上报风险"');
    expect(dashboardView).toContain("draftProjectName()");
    expect(dashboardView).toContain("v-model=\"interactionFields.category\"");
    expect(dashboardView).toContain("v-model=\"interactionFields.suggestion\"");
    expect(dashboardView).toContain("watch(");
    expect(dashboardView).toContain("syncInteractionFields");
    expect(dashboardView).toContain("风险标题和风险描述不能为空");
    expect(dashboardView).not.toContain(":placeholder=\"draftField");
  });

  it("offers a new conversation for stale config or an oversize context", () => {
    expect(dashboardView).toContain(
      "agent.state.error.code === 'AGENT_EXECUTION_CONFIG_INVALID'",
    );
    expect(dashboardView).toContain(
      "agent.state.error.code === 'AGENT_CONTEXT_TOO_LARGE'",
    );
    expect(dashboardView).toContain("v-else-if=\"");
    expect(dashboardView).toContain("@click=\"startNewAgentConversation\"");
  });

  it("uses a styled retry control for weekly-report errors", () => {
    expect(dashboardView).toContain('class="weekly-report-state is-error"');
    expect(dashboardView).toContain('class="admin-outline-button weekly-state-button"');
    expect(dashboardView).toContain("同步周报操作不受影响");
  });

  it("keeps keyword deletion within an accessible chip", () => {
    expect(mailboxSettingsView).toContain('class="keyword-chip"');
    expect(mailboxSettingsView).toContain("删除关键词“${item}”");
    expect(mailboxSettingsView).toContain('class="keyword-input"');
    expect(systemConfigView).toContain('class="keyword-chip"');
    expect(systemConfigView).toContain("删除风险关键词“${keyword}”");
  });

  it("uses one CSS counter as the data-flow number source", () => {
    const flowSection = mailboxSettingsView.slice(
      mailboxSettingsView.indexOf("sync-flow-card"),
      mailboxSettingsView.indexOf('class="prototype-security-banner mailbox-security"'),
    );

    expect(flowSection).toContain('class="sync-flow-list"');
    expect(flowSection).not.toMatch(/<b>\d<\/b>/);
    expect(mailboxSettingsView).toContain("counter-reset:flow-step");
    expect(mailboxSettingsView).toContain("content:counter(flow-step)");
  });

  it("does not expose an inactive notification placeholder or a fake unread count", () => {
    expect(adminShell).not.toContain('aria-label="查看后台通知"');
    expect(adminShell).not.toContain("<b>2</b>");
    expect(businessHeader).not.toContain('class="notice-tool"');
    expect(businessHeader).not.toContain("<b>3</b>");
  });

  it("keeps Agent project selection compact and search-ready", () => {
    expect(dashboardView).not.toContain("以上都不是，重新搜索");
    expect(dashboardView).not.toContain("manualProjectSearchOpen");
    expect(dashboardView).toContain('class="project-manual-search"');
    expect(dashboardView).toContain(
      "'is-completed': agent.state.interaction.status !== 'OPEN'",
    );
    expect(dashboardView).toContain("max-height:280px");
    expect(dashboardView).toContain("overflow-y:auto");
    expect(dashboardView).toContain("animation:project-selection-dismiss");
    expect(dashboardView).toContain("@keyframes project-selection-dismiss");
  });

  it("turns the send button into a durable stop button while a turn is streaming", () => {
    // The agent form still submits via sendAgent() — the stop button is a
    // type="button" so it never triggers a form submit / re-send.
    expect(dashboardView).toContain('@submit.prevent="sendAgent()"');
    expect(dashboardView).toContain("const agentCanStop = computed(");
    // The stop control is shown only while a durable execution is active
    // (streaming / disconnected); otherwise the submit button is shown.
    expect(dashboardView).toContain('v-if="agentCanStop"');
    expect(dashboardView).toContain('class="agent-stop"');
    expect(dashboardView).toContain('type="button"');
    // 停止 POSTs the explicit durable cancel — it does not just abort fetch.
    expect(dashboardView).toContain('@click="agent.cancel()"');
    expect(dashboardView).toContain("停止");
    // The 2-column textarea+button grid is unchanged (no third button column).
    expect(prototypePageStyles).toContain("grid-template-columns:1fr auto");
    // The stop button gets a distinct red modifier over the shared base.
    expect(prototypePageStyles).toContain("button.agent-stop");
  });

});
