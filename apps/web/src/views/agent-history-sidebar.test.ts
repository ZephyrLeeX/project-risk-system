import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const sidebar = readFileSync(
  new URL("../components/agent/AgentHistorySidebar.vue", import.meta.url),
  "utf8",
);
const dashboardView = readFileSync(
  new URL("./DashboardView.vue", import.meta.url),
  "utf8",
);
const prototypePageStyles = readFileSync(
  new URL("../styles/prototype-pages.css", import.meta.url),
  "utf8",
);

describe("agent history sidebar layout regressions", () => {
  it("persists only the collapse preference, never conversation content", () => {
    expect(sidebar).toContain(
      '"risk-system.agent.history-sidebar-collapsed"',
    );
    // localStorage is touched only through COLLAPSED_KEY — no conversation
    // titles, ids or messages are persisted by the sidebar.
    const localStorageCalls = sidebar.match(/localStorage\.\w+\([^)]*\)/g) ?? [];
    expect(localStorageCalls.length).toBeGreaterThanOrEqual(2);
    for (const call of localStorageCalls) {
      expect(call).toContain("COLLAPSED_KEY");
    }
  });

  it("keeps the sidebar usable while collapsed: expand + new conversation stay reachable", () => {
    // The toolbar (collapse toggle + new-conversation) renders regardless of
    // collapse state — only the list scroll container is hidden.
    expect(sidebar).toContain('v-if="!isCollapsed" class="agent-history-scroll"');
    expect(sidebar).not.toContain('v-if="!collapsed" class="agent-history-icon-button');
    expect(sidebar).toContain('aria-label="新建会话"');
    expect(sidebar).toContain(
      ':aria-label="collapsed ? \'展开历史会话\' : \'收起历史会话\'"',
    );
    expect(sidebar).toContain(".agent-history-sidebar.is-collapsed{width:52px");
  });

  it("renders as a static desktop column and an overlay drawer below 900px", () => {
    expect(prototypePageStyles).toContain(".agent-drawer{display:flex");
    expect(prototypePageStyles).toContain(
      ".agent-main{position:relative;display:flex;min-width:0;min-height:0;flex:1;flex-direction:column}",
    );
    expect(prototypePageStyles).toContain(
      ".agent-history-toggle{display:none",
    );
    expect(prototypePageStyles).toContain(
      "@media(max-width:899px){.agent-history-toggle{display:inline-block}",
    );
    expect(dashboardView).toContain('class="agent-history-toggle"');
    expect(dashboardView).toContain("@click=\"agentHistoryDrawerOpen = true\"");
    // The mobile drawer closes on backdrop click, Esc and conversation select.
    expect(sidebar).toContain('class="agent-history-scrim"');
    expect(sidebar).toContain('if (event.key !== "Escape") return');
    expect(sidebar).toContain('emit("update:mobileOpen", false);');
  });

  it("groups conversations by 今天 / 昨天 / 更早 and marks the current one", () => {
    expect(sidebar).toContain('label: "今天"');
    expect(sidebar).toContain('label: "昨天"');
    expect(sidebar).toContain('label: "更早"');
    expect(sidebar).toContain(
      ':aria-current="item.id === currentConversationId ? \'true\' : undefined"',
    );
    expect(sidebar).toContain(
      "'is-current': item.id === currentConversationId",
    );
  });

  it("keeps delete behind the per-row menu with a danger-styled confirmation", () => {
    expect(sidebar).toContain("删除会话");
    expect(sidebar).toContain("requestDelete");
    expect(dashboardView).toContain('title="删除历史会话？"');
    expect(dashboardView).toContain("删除后该会话将不再出现在历史记录中。");
    expect(dashboardView).toContain("@click=\"confirmAgentConversationDelete\"");
    expect(dashboardView).toContain("admin-danger-button");
  });
});

describe("agent history sidebar data loading", () => {
  it("loads exactly one page (20 rows) per open and grows via explicit load-more", () => {
    expect(dashboardView).toContain(
      "const AGENT_HISTORY_PAGE_SIZE = 20;",
    );
    expect(dashboardView).toContain(
      "await agentApi.listConversations(1, AGENT_HISTORY_PAGE_SIZE)",
    );
    // No surface preloads per-conversation history; switching fetches it on click.
    expect(dashboardView).toContain(
      "await agentApi.listConversations(\n      agentHistoryPage.value + 1,\n      AGENT_HISTORY_PAGE_SIZE,\n    )",
    );
    expect(sidebar).toContain('"load-more": []');
    expect(dashboardView).toContain("@load-more=\"loadMoreAgentHistory\"");
  });

  it("never duplicates an already-loaded conversation when appending a page", () => {
    expect(dashboardView).toContain(
      "const loaded = new Set(agentHistory.value.map((item) => item.id));",
    );
    expect(dashboardView).toContain(
      "page.items.filter((item) => !loaded.has(item.id))",
    );
  });

  it("refreshes page 1 on turn completion instead of every SSE frame", () => {
    expect(dashboardView).toContain(
      "watch(\n  () => agent.state.status,",
    );
    expect(dashboardView).toContain('if (status !== "completed") return');
    expect(dashboardView).toContain(
      'previous === "streaming" || previous === "cancelling" || previous === null',
    );
  });

  it("starts a new conversation through the composable reset, never a reload", () => {
    expect(dashboardView).toContain("function startNewAgentConversation(): void");
    expect(dashboardView).toContain("agent.reset();");
    expect(dashboardView).not.toContain("window.location.reload");
    expect(dashboardView).toContain("@new-conversation=\"startNewAgentConversation\"");
  });
});

describe("agent history sidebar delete flow", () => {
  it("maps the busy 409 to the stop-first guidance", () => {
    expect(dashboardView).toContain(
      'reason.status === 409 &&\n      reason.code === "AGENT_CONVERSATION_BUSY"',
    );
    expect(dashboardView).toContain("当前会话仍在执行，请先停止后再删除。");
  });

  it("returns to a fresh new-conversation state after deleting the visible conversation", () => {
    expect(dashboardView).toContain(
      "if (conversationId === agent.state.conversationId) {",
    );
    expect(dashboardView).toContain("startNewAgentConversation();");
  });

  it("removes the deleted row locally and keeps the total in sync", () => {
    expect(dashboardView).toContain(
      "agentHistory.value.filter((item) => item.id !== conversationId)",
    );
    expect(dashboardView).toContain(
      "Math.max(0, agentHistoryTotal.value - 1)",
    );
  });
});
