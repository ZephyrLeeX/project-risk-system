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
const prototypePageStyles = readFileSync(
  new URL("../styles/prototype-pages.css", import.meta.url),
  "utf8",
);

describe("live UI layout regressions", () => {
  it("renders Agent capabilities as separate, wrappable chips", () => {
    expect(dashboardView).toContain('class="agent-tool-list"');
    expect(dashboardView).toContain('v-for="tool in agentHelp.tools"');
    expect(prototypePageStyles).toContain(".agent-tool-list{display:flex");
    expect(prototypePageStyles).toContain("flex-wrap:wrap");
  });

  it("keeps Agent suggestions content-sized instead of assigning them a viewport row", () => {
    expect(dashboardView).toContain("v-for=\"prompt in agentSuggestions\"");
    expect(prototypePageStyles).toContain(".agent-suggestions{display:grid");
    expect(prototypePageStyles).toContain("repeat(auto-fit,minmax(150px,1fr))");
    expect(prototypePageStyles).toContain(".agent-drawer{display:flex");
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
});
