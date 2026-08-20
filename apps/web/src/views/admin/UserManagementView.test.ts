import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const view = readFileSync(
  new URL("./UserManagementView.vue", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../../styles/base.css", import.meta.url),
  "utf8",
);

describe("user management owned-project binding regressions", () => {
  it("keeps the name-based recommendation purely client-side", () => {
    // The recommended list is derived from the current form displayName, never
    // auto-submitted as an authorization decision.
    expect(view).toContain("normalizeName(project.deliveryOwnerName)");
    expect(view).toContain("normalizeName(form.displayName)");
    expect(view).toContain("deliveryOwnerName &&");
  });

  it("only surfaces the owned selector for PROJECT_MANAGER + OWNED scopes", () => {
    expect(view).toContain('selectedRole.value.code === "PROJECT_MANAGER"');
    expect(view).toContain('["OWNED", "OWNED_OR_ASSIGNED"].includes(form.dataScope)');
    expect(view).toContain('v-if="ownedProjectsVisible" class="owned-project-selector"');
  });

  it("limits scopes to the server-owned role boundary", () => {
    // The boundary comes from RoleResponse.allowedDataScopes (single source:
    // apps/api admin/users/policy.py) — no local role→scope copy to drift.
    expect(view).toContain("selectedRole.value?.allowedDataScopes");
    expect(view).toContain("availableScopeOptions");
    expect(view).toContain('v-for="scope in availableScopeOptions"');
    expect(view).not.toContain("allowedScopeValuesByRole");
    expect(view).toContain('selectedRole.value.code !== "PROJECT_MANAGER"');
    expect(view).toContain("form.ownedProjectIds = []");
  });

  it("warns instead of silently resetting an edited user's scope", () => {
    expect(view).toContain("scopeResetNotice");
    expect(view).toContain('v-if="scopeResetNotice" class="scope-reset-notice"');
    expect(view).toContain("原数据范围不适用于");
    expect(view).toContain("scopeResetNotice.value = \"\"");
  });

  it("offers select-all and cancel-all over the recommended projects", () => {
    expect(view).toContain("function toggleAllRecommended(): void");
    expect(view).toContain("function cancelAllRecommended(): void");
    expect(view).toContain("allRecommendedSelected");
    expect(view).toContain("全选推荐项目");
    expect(view).toContain("取消全选");
  });

  it("shows per-project ownership state including conflicts", () => {
    expect(view).toContain("function ownedProjectStatus(project: ProjectOption): string");
    expect(view).toContain("尚未绑定系统账号");
    expect(view).toContain("当前已负责");
    expect(view).toContain("已由 ${project.managerName ?? \"其他用户\"} 负责");
    expect(view).toContain("function ownedProjectBlocked(project: ProjectOption): boolean");
    expect(view).toContain(':disabled="ownedProjectBlocked(project)"');
    expect(view).toContain('class="owned-project-row"');
    expect(view).toContain("is-conflict");
  });

  it("keeps manual search for projects that never matched by name", () => {
    expect(view).toContain("全部项目 / 搜索项目");
    expect(view).toContain('v-model="ownedProjectKeyword"');
    expect(view).toContain("按项目名或编码搜索");
    expect(view).toContain("ownedSearchProjects");
  });

  it("round-trips ownedProjectIds on edit and save", () => {
    expect(view).toContain("ownedProjectIds: [...user.ownedProjectIds]");
    expect(view).toContain("ownedProjectIds: [...form.ownedProjectIds]");
    expect(view).toContain("ownedProjectCount");
  });

  it("styles the recommended and search project lists", () => {
    expect(styles).toContain(".owned-project-selector");
    expect(styles).toContain(".owned-selector-actions button");
    expect(styles).toContain(".owned-recommend-note");
    expect(styles).toContain(".owned-project-row.is-conflict");
    expect(styles).toContain(".owned-search-head input");
  });
});
