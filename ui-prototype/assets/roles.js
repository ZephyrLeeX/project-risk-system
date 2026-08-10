(() => {
  const body = document.body;
  const sidebar = document.querySelector("#adminSidebar");
  const sidebarToggle = document.querySelector("#sidebarToggle");
  const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
  const profileButton = document.querySelector("#adminProfileButton");
  const profileMenu = document.querySelector("#adminProfileMenu");
  const noticeButton = document.querySelector("#noticeButton");
  const roleList = document.querySelector("#roleList");
  const directoryCount = document.querySelector("#directoryCount");
  const roleTotalCount = document.querySelector("#roleTotalCount");
  const selectedRoleIcon = document.querySelector("#selectedRoleIcon");
  const selectedRoleName = document.querySelector("#selectedRoleName");
  const selectedRoleType = document.querySelector("#selectedRoleType");
  const selectedRoleStatus = document.querySelector("#selectedRoleStatus");
  const selectedRoleCode = document.querySelector("#selectedRoleCode");
  const selectedRoleUsers = document.querySelector("#selectedRoleUsers");
  const selectedRoleDescription = document.querySelector("#selectedRoleDescription");
  const selectedRoleScopeSummary = document.querySelector("#selectedRoleScopeSummary");
  const selectedPermissionCount = document.querySelector("#selectedPermissionCount");
  const selectedRoleUpdated = document.querySelector("#selectedRoleUpdated");
  const roleMoreButton = document.querySelector("#roleMoreButton");
  const roleMoreMenu = document.querySelector("#roleMoreMenu");
  const copyRoleButton = document.querySelector("#copyRoleButton");
  const viewRoleUsersButton = document.querySelector("#viewRoleUsersButton");
  const permissionSearch = document.querySelector("#permissionSearch");
  const permissionGroups = document.querySelector("#permissionGroups");
  const permissionEmpty = document.querySelector("#permissionEmpty");
  const scopeOptions = document.querySelector("#scopeOptions");
  const scopeConstraintNote = document.querySelector("#scopeConstraintNote");
  const unsavedBar = document.querySelector("#unsavedBar");
  const unsavedCount = document.querySelector("#unsavedCount");
  const discardChangesButton = document.querySelector("#discardChangesButton");
  const resetRoleButton = document.querySelector("#resetRoleButton");
  const saveRoleButton = document.querySelector("#saveRoleButton");
  const addRoleButton = document.querySelector("#addRoleButton");
  const roleDrawer = document.querySelector("#roleDrawer");
  const roleForm = document.querySelector("#roleForm");
  const roleDrawerEyebrow = document.querySelector("#roleDrawerEyebrow");
  const roleDrawerTitle = document.querySelector("#roleDrawerTitle");
  const roleNameInput = document.querySelector("#roleNameInput");
  const roleCodeInput = document.querySelector("#roleCodeInput");
  const roleDescriptionInput = document.querySelector("#roleDescriptionInput");
  const copyFromInput = document.querySelector("#copyFromInput");
  const copyFromSelect = document.querySelector("#copyFromSelect");
  const roleEnabledInput = document.querySelector("#roleEnabledInput");
  const saveRoleFormButton = document.querySelector("#saveRoleFormButton");
  const permissionMatrixButton = document.querySelector("#permissionMatrixButton");
  const matrixModal = document.querySelector("#matrixModal");
  const matrixTableBody = document.querySelector("#matrixTableBody");
  const confirmModal = document.querySelector("#roleConfirmModal");
  const confirmTitle = document.querySelector("#roleConfirmTitle");
  const confirmCopy = document.querySelector("#roleConfirmCopy");
  const confirmTip = document.querySelector("#roleConfirmTip");
  const confirmButton = document.querySelector("#roleConfirmButton");
  const toast = document.querySelector("#rolesToast");
  const toastCopy = toast?.querySelector("p");

  const permissionDefinitions = [
    {
      group: "base",
      groupName: "业务端菜单与基础访问",
      groupClass: "group-base",
      key: "dashboard.view",
      name: "查看 Web 风险看板",
      description: "查看风险、回款、周报和待办等业务视图"
    },
    {
      group: "base",
      groupName: "业务端菜单与基础访问",
      groupClass: "group-base",
      key: "agent.use",
      name: "使用 Agent 智能查询",
      description: "在授权项目范围内进行风险和回款查询"
    },
    {
      group: "risk",
      groupName: "风险业务操作",
      groupClass: "group-risk",
      key: "risk.report",
      name: "上报风险",
      description: "通过日常上报录入新的项目风险"
    },
    {
      group: "risk",
      groupName: "风险业务操作",
      groupClass: "group-risk",
      key: "risk.resolve",
      name: "处理与解除风险",
      description: "更新处理情况并提交风险解除"
    },
    {
      group: "risk",
      groupName: "风险业务操作",
      groupClass: "group-risk",
      key: "risk.manage_all",
      name: "管理全量风险",
      description: "修改全量风险分类、等级、内容并执行治理操作"
    },
    {
      group: "mailbox",
      groupName: "个人邮箱与周报同步",
      groupClass: "group-mailbox",
      key: "mailbox.manage_self",
      name: "配置本人邮箱",
      description: "仅配置本人邮箱授权，不允许管理员代配"
    },
    {
      group: "mailbox",
      groupName: "个人邮箱与周报同步",
      groupClass: "group-mailbox",
      key: "mailbox.sync_self",
      name: "手动同步本人邮箱",
      description: "触发本人邮箱周报同步并查看同步结果"
    },
    {
      group: "admin",
      groupName: "后台管理与系统治理",
      groupClass: "group-admin",
      key: "admin.user.manage",
      name: "用户管理",
      description: "新增、编辑、启停用户并重置密码"
    },
    {
      group: "admin",
      groupName: "后台管理与系统治理",
      groupClass: "group-admin",
      key: "admin.role.manage",
      name: "角色与权限管理",
      description: "维护角色、菜单、操作权限和权限边界"
    },
    {
      group: "admin",
      groupName: "后台管理与系统治理",
      groupClass: "group-admin",
      key: "admin.scope.manage",
      name: "项目数据范围管理",
      description: "配置全部、本人负责或被授权项目范围"
    },
    {
      group: "admin",
      groupName: "后台管理与系统治理",
      groupClass: "group-admin",
      key: "admin.ai.manage",
      name: "AI 服务与 API Key 管理",
      description: "维护AI服务、API Key、默认模型和连通性"
    },
    {
      group: "admin",
      groupName: "后台管理与系统治理",
      groupClass: "group-admin",
      key: "admin.import.manage",
      name: "Excel 数据导入",
      description: "上传、校验、提交和回滚项目清单及回款批次"
    },
    {
      group: "admin",
      groupName: "后台管理与系统治理",
      groupClass: "group-admin",
      key: "admin.config.manage",
      name: "系统配置",
      description: "维护风险等级、分类、通知和业务字典"
    },
    {
      group: "admin",
      groupName: "后台管理与系统治理",
      groupClass: "group-admin",
      key: "admin.audit.view",
      name: "查看审计日志",
      description: "按授权范围查询登录、配置和数据变更记录"
    }
  ];

  const createRole = ({
    code,
    name,
    description,
    users,
    permissions,
    scope,
    allowedScopes,
    isSystem = true,
    status = "ACTIVE",
    updated = "2026-07-22 17:26",
    iconClass
  }) => ({
    code,
    name,
    description,
    users,
    isSystem,
    status,
    iconClass,
    permissions: new Set(permissions),
    savedPermissions: new Set(permissions),
    defaultPermissions: new Set(permissions),
    scope,
    savedScope: scope,
    defaultScope: scope,
    allowedScopes,
    updated
  });

  const roles = {
    SYSTEM_ADMIN: createRole({
      code: "SYSTEM_ADMIN",
      name: "系统管理员",
      description: "负责用户、角色、项目范围、Excel导入、AI服务和系统配置等后台治理能力。",
      users: 2,
      permissions: [
        "dashboard.view",
        "admin.user.manage",
        "admin.role.manage",
        "admin.scope.manage",
        "admin.ai.manage",
        "admin.import.manage",
        "admin.config.manage",
        "admin.audit.view"
      ],
      scope: "ALL",
      allowedScopes: ["ALL"],
      iconClass: "role-icon-admin"
    }),
    RISK_ADMIN: createRole({
      code: "RISK_ADMIN",
      name: "风险管理员",
      description: "负责全项目风险审核、分类等级调整、发布治理，以及本人邮箱和周报同步。",
      users: 4,
      permissions: [
        "dashboard.view",
        "agent.use",
        "risk.report",
        "risk.resolve",
        "risk.manage_all",
        "mailbox.manage_self",
        "mailbox.sync_self"
      ],
      scope: "ALL",
      allowedScopes: ["ALL"],
      updated: "2026-07-12 10:42",
      iconClass: "role-icon-risk"
    }),
    PROJECT_MANAGER: createRole({
      code: "PROJECT_MANAGER",
      name: "项目经理",
      description: "查看本人负责或被授权项目，上报、处理和解除职责范围内的风险。",
      users: 22,
      permissions: ["dashboard.view", "agent.use", "risk.report", "risk.resolve"],
      scope: "OWNED_OR_ASSIGNED",
      allowedScopes: ["OWNED", "ASSIGNED", "OWNED_OR_ASSIGNED", "NONE"],
      iconClass: "role-icon-manager"
    }),
    VIEWER_AUDITOR: createRole({
      code: "VIEWER_AUDITOR",
      name: "查看/审计员",
      description: "在被授权项目范围内只读查看风险、回款、周报和相关审计信息。",
      users: 4,
      permissions: ["dashboard.view", "agent.use", "admin.audit.view"],
      scope: "ASSIGNED",
      allowedScopes: ["ASSIGNED", "NONE"],
      updated: "2026-07-18 14:05",
      iconClass: "role-icon-auditor"
    })
  };

  const scopeNames = {
    ALL: "全部项目",
    OWNED: "本人负责项目",
    ASSIGNED: "被授权项目",
    OWNED_OR_ASSIGNED: "本人负责及授权项目",
    NONE: "无项目数据"
  };

  const systemOnlyPermissions = new Set([
    "admin.user.manage",
    "admin.role.manage",
    "admin.scope.manage",
    "admin.ai.manage",
    "admin.import.manage",
    "admin.config.manage"
  ]);
  const mailboxPermissions = new Set(["mailbox.manage_self", "mailbox.sync_self"]);
  const riskManagerPermissions = new Set(["risk.report", "risk.resolve"]);

  let currentRoleCode = "SYSTEM_ADMIN";
  let drawerMode = "create";
  let drawerTargetCode = "";
  let pendingConfirmAction = null;
  let previousFocus = null;
  let toastTimer = null;

  const currentRole = () => roles[currentRoleCode];

  const showToast = (message) => {
    if (!toast || !toastCopy) return;
    window.clearTimeout(toastTimer);
    toastCopy.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 2800);
  };

  const setOverlayState = () => {
    body.classList.toggle(
      "roles-overlay-open",
      !roleDrawer.hidden || !matrixModal.hidden || !confirmModal.hidden
    );
  };

  const pad = (value) => String(value).padStart(2, "0");

  const formatDateTime = (date) =>
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;

  const closeSidebar = () => {
    sidebar.classList.remove("is-open");
    sidebarToggle.setAttribute("aria-expanded", "false");
    sidebarBackdrop.hidden = true;
  };

  const closeMenus = (except = null) => {
    [roleMoreMenu, copyFromSelect.querySelector(".role-form-select-menu")].forEach((menu) => {
      if (menu === except) return;
      menu.hidden = true;
      const trigger = menu === roleMoreMenu
        ? roleMoreButton
        : copyFromSelect.querySelector(".role-form-select-trigger");
      trigger.setAttribute("aria-expanded", "false");
    });
  };

  const permissionBoundary = (roleCode, permissionKey) => {
    const isCustom = !roles[roleCode]?.isSystem;

    if (permissionKey === "dashboard.view" && roles[roleCode]?.isSystem) {
      return {
        disabled: true,
        forced: true,
        label: "默认角色必需"
      };
    }

    if (mailboxPermissions.has(permissionKey)) {
      return {
        disabled: true,
        forced: roleCode === "RISK_ADMIN",
        label: "仅风险管理员"
      };
    }

    if (systemOnlyPermissions.has(permissionKey)) {
      return {
        disabled: true,
        forced: roleCode === "SYSTEM_ADMIN",
        label: "仅系统管理员"
      };
    }

    if (permissionKey === "risk.manage_all") {
      return {
        disabled: true,
        forced: roleCode === "RISK_ADMIN",
        label: "仅风险管理员"
      };
    }

    if (permissionKey === "admin.audit.view") {
      if (roleCode === "SYSTEM_ADMIN") {
        return { disabled: true, forced: true, label: "后台治理必需" };
      }
      if (roleCode === "VIEWER_AUDITOR") {
        return { disabled: false, forced: null, label: "" };
      }
      return {
        disabled: true,
        forced: false,
        label: isCustom ? "仅系统或审计角色" : "角色边界"
      };
    }

    if (permissionKey === "agent.use" && roleCode === "SYSTEM_ADMIN") {
      return { disabled: true, forced: false, label: "业务角色权限" };
    }

    if (riskManagerPermissions.has(permissionKey)) {
      const allowed =
        roleCode === "RISK_ADMIN" || roleCode === "PROJECT_MANAGER" || isCustom;
      if (!allowed) return { disabled: true, forced: false, label: "风险业务角色" };
    }

    return { disabled: false, forced: null, label: "" };
  };

  const enforceRoleBoundaries = (role) => {
    permissionDefinitions.forEach(({ key }) => {
      const boundary = permissionBoundary(role.code, key);
      if (boundary.forced === true) role.permissions.add(key);
      if (boundary.forced === false) role.permissions.delete(key);
    });
    if (!role.allowedScopes.includes(role.scope)) {
      role.scope = role.allowedScopes[0] ?? "NONE";
    }
  };

  Object.values(roles).forEach(enforceRoleBoundaries);

  const permissionDifferenceCount = (role) => {
    const allKeys = new Set([...role.permissions, ...role.savedPermissions]);
    let count = 0;
    allKeys.forEach((key) => {
      if (role.permissions.has(key) !== role.savedPermissions.has(key)) count += 1;
    });
    if (role.scope !== role.savedScope) count += 1;
    return count;
  };

  const updateDirtyState = () => {
    const count = permissionDifferenceCount(currentRole());
    unsavedCount.textContent = String(count);
    unsavedBar.hidden = count === 0;
    saveRoleButton.disabled = count === 0;
  };

  const updateRoleListMeta = (role) => {
    const item = roleList.querySelector(`[data-role-code="${role.code}"]`);
    if (!item) return;
    item.querySelector(".role-list-copy strong").textContent = role.name;
    item.querySelector(".role-list-meta b").textContent = `${role.users}人`;
    item.querySelector(".role-list-meta i").textContent = role.isSystem ? "系统预置" : "自定义";
    item.querySelector(".role-list-meta i").classList.toggle("is-custom", !role.isSystem);
  };

  const createRoleListItem = (role) => {
    const button = document.createElement("button");
    button.className = "role-list-item";
    button.type = "button";
    button.dataset.roleCode = role.code;
    button.setAttribute("aria-pressed", "false");

    const icon = document.createElement("span");
    icon.className = `role-list-icon ${role.iconClass}`;
    icon.setAttribute("aria-hidden", "true");

    const copy = document.createElement("span");
    copy.className = "role-list-copy";
    const name = document.createElement("strong");
    name.textContent = role.name;
    const code = document.createElement("small");
    code.textContent = role.code;
    copy.append(name, code);

    const meta = document.createElement("span");
    meta.className = "role-list-meta";
    const users = document.createElement("b");
    users.textContent = `${role.users}人`;
    const type = document.createElement("i");
    type.className = "is-custom";
    type.textContent = "自定义";
    meta.append(users, type);

    button.append(icon, copy, meta);
    return button;
  };

  const renderPermissions = () => {
    const role = currentRole();
    enforceRoleBoundaries(role);
    const keyword = permissionSearch.value.trim().toLowerCase();
    const grouped = new Map();

    permissionDefinitions.forEach((definition) => {
      const searchable = `${definition.name} ${definition.key} ${definition.description}`.toLowerCase();
      if (keyword && !searchable.includes(keyword)) return;
      if (!grouped.has(definition.group)) {
        grouped.set(definition.group, {
          name: definition.groupName,
          className: definition.groupClass,
          items: []
        });
      }
      grouped.get(definition.group).items.push(definition);
    });

    permissionGroups.replaceChildren(
      ...[...grouped.values()].map((group) => {
        const section = document.createElement("section");
        section.className = `permission-group ${group.className}`;

        const header = document.createElement("header");
        const headingWrap = document.createElement("div");
        const icon = document.createElement("span");
        icon.className = "permission-group-icon";
        icon.setAttribute("aria-hidden", "true");
        const heading = document.createElement("h4");
        heading.textContent = group.name;
        headingWrap.append(icon, heading);
        const count = document.createElement("span");
        const granted = group.items.filter(({ key }) => role.permissions.has(key)).length;
        count.textContent = `${granted}/${group.items.length} 已授权`;
        header.append(headingWrap, count);

        const list = document.createElement("ul");
        list.className = "permission-list";
        group.items.forEach((definition) => {
          const item = document.createElement("li");
          item.className = "permission-item";
          item.dataset.permissionKey = definition.key;

          const copy = document.createElement("div");
          copy.className = "permission-copy";
          const titleRow = document.createElement("span");
          const title = document.createElement("strong");
          title.textContent = definition.name;
          const code = document.createElement("code");
          code.textContent = definition.key;
          titleRow.append(title, code);

          const boundary = permissionBoundary(role.code, definition.key);
          if (boundary.label) {
            const lock = document.createElement("i");
            lock.className = "permission-lock";
            lock.textContent = boundary.label;
            titleRow.append(lock);
          }

          const description = document.createElement("small");
          description.textContent = definition.description;
          copy.append(titleRow, description);

          const toggle = document.createElement("label");
          toggle.className = "permission-switch";
          const input = document.createElement("input");
          input.type = "checkbox";
          input.dataset.permissionToggle = definition.key;
          input.checked = role.permissions.has(definition.key);
          input.disabled = boundary.disabled;
          input.setAttribute("aria-label", `${definition.name}权限`);
          const visual = document.createElement("span");
          toggle.append(input, visual);
          item.append(copy, toggle);
          list.append(item);
        });

        section.append(header, list);
        return section;
      })
    );

    permissionEmpty.hidden = grouped.size !== 0;
    selectedPermissionCount.textContent = String(role.permissions.size);
  };

  const renderScopeOptions = () => {
    const role = currentRole();
    scopeOptions.querySelectorAll("label").forEach((label) => {
      const input = label.querySelector("input");
      const allowed = role.allowedScopes.includes(input.value);
      input.disabled = !allowed;
      input.checked = input.value === role.scope;
      label.classList.toggle("is-locked", !allowed);
    });

    if (role.code === "SYSTEM_ADMIN" || role.code === "RISK_ADMIN") {
      scopeConstraintNote.textContent = `${role.name}承担全局职责，项目数据范围固定为“全部项目”，不能缩小或扩展为其他范围。`;
    } else if (role.code === "PROJECT_MANAGER") {
      scopeConstraintNote.textContent = "项目经理可使用本人负责、被授权或二者并集；所有项目查询均按用户与项目关系实时过滤。";
    } else if (role.code === "VIEWER_AUDITOR") {
      scopeConstraintNote.textContent = "查看/审计员仅允许访问明确授权项目，且业务数据保持只读。";
    } else {
      scopeConstraintNote.textContent = "自定义角色不能获得全部项目范围，需通过负责人关系或项目授权控制数据边界。";
    }
  };

  const renderRole = () => {
    const role = currentRole();
    enforceRoleBoundaries(role);

    roleList.querySelectorAll("[data-role-code]").forEach((item) => {
      const selected = item.dataset.roleCode === role.code;
      item.classList.toggle("is-selected", selected);
      item.setAttribute("aria-pressed", String(selected));
    });

    selectedRoleIcon.className = `selected-role-icon ${role.iconClass}`;
    selectedRoleName.textContent = role.name;
    selectedRoleType.textContent = role.isSystem ? "系统预置" : "自定义";
    selectedRoleType.classList.toggle("is-custom", !role.isSystem);
    selectedRoleStatus.className =
      role.status === "ACTIVE" ? "role-status-badge" : "role-status-badge is-disabled";
    selectedRoleStatus.innerHTML =
      role.status === "ACTIVE" ? "<i></i>启用" : "<i></i>停用";
    selectedRoleCode.textContent = role.code;
    selectedRoleUsers.textContent = String(role.users);
    selectedRoleDescription.textContent = role.description;
    selectedRoleScopeSummary.textContent = scopeNames[role.scope];
    selectedRoleUpdated.textContent = role.updated;

    const deleteButton = roleMoreMenu.querySelector("[data-role-action='delete']");
    deleteButton.disabled = role.isSystem;
    deleteButton.textContent = role.isSystem ? "系统预置角色不可删除" : "删除自定义角色";
    const statusButton = roleMoreMenu.querySelector("[data-role-action='toggle-status']");
    statusButton.textContent = role.status === "ACTIVE" ? "停用角色" : "启用角色";

    renderPermissions();
    renderScopeOptions();
    updateDirtyState();
  };

  roleList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-role-code]");
    if (!item || !roles[item.dataset.roleCode]) return;
    currentRoleCode = item.dataset.roleCode;
    permissionSearch.value = "";
    closeMenus();
    renderRole();
  });

  permissionGroups.addEventListener("change", (event) => {
    const input = event.target.closest("[data-permission-toggle]");
    if (!input || input.disabled) return;
    const role = currentRole();
    if (input.checked) role.permissions.add(input.dataset.permissionToggle);
    else role.permissions.delete(input.dataset.permissionToggle);
    renderPermissions();
    updateDirtyState();
  });

  scopeOptions.addEventListener("change", (event) => {
    const input = event.target.closest("input[name='roleScope']");
    if (!input || input.disabled) return;
    currentRole().scope = input.value;
    selectedRoleScopeSummary.textContent = scopeNames[input.value];
    updateDirtyState();
  });

  permissionSearch.addEventListener("input", renderPermissions);

  saveRoleButton.addEventListener("click", () => {
    const role = currentRole();
    role.savedPermissions = new Set(role.permissions);
    role.savedScope = role.scope;
    role.updated = formatDateTime(new Date());
    selectedRoleUpdated.textContent = role.updated;
    updateDirtyState();
    showToast(`“${role.name}”的权限配置已保存，并记录到审计日志。`);
  });

  discardChangesButton.addEventListener("click", () => {
    const role = currentRole();
    role.permissions = new Set(role.savedPermissions);
    role.scope = role.savedScope;
    renderRole();
    showToast(`已撤销“${role.name}”尚未保存的修改。`);
  });

  resetRoleButton.addEventListener("click", () => {
    const role = currentRole();
    role.permissions = new Set(role.defaultPermissions);
    role.scope = role.defaultScope;
    enforceRoleBoundaries(role);
    renderRole();
    showToast(`“${role.name}”已恢复为默认权限，点击保存后生效。`);
  });

  const setCopyFromValue = (value) => {
    const menu = copyFromSelect.querySelector(".role-form-select-menu");
    const option = menu.querySelector(`[data-value="${value}"]`) ?? menu.querySelector("[data-value='']");
    copyFromInput.value = option.dataset.value;
    copyFromSelect.querySelector(".role-form-select-trigger span").textContent = option.textContent;
    menu.querySelectorAll("[data-value]").forEach((item) => {
      item.classList.toggle("is-selected", item === option);
    });
  };

  const clearRoleFormErrors = () => {
    roleForm.querySelectorAll(".role-form-field").forEach((field) => {
      field.classList.remove("is-invalid");
      const errors = field.querySelectorAll(".role-field-error");
      errors.forEach((error) => {
        error.textContent = "";
      });
    });
  };

  const setRoleFormError = (input, message) => {
    const field = input.closest(".role-form-field");
    field.classList.add("is-invalid");
    field.querySelector(".role-field-error").textContent = message;
  };

  const openRoleDrawer = (mode, sourceCode = "") => {
    previousFocus = document.activeElement;
    drawerMode = mode;
    drawerTargetCode = mode === "edit" ? sourceCode : "";
    roleForm.reset();
    clearRoleFormErrors();
    roleEnabledInput.checked = true;
    roleCodeInput.disabled = mode === "edit";
    roleNameInput.disabled = false;
    setCopyFromValue("");

    if (mode === "create") {
      roleDrawerEyebrow.textContent = "CREATE ROLE";
      roleDrawerTitle.textContent = "新增自定义角色";
      saveRoleFormButton.textContent = "创建角色";
    }

    if (mode === "copy") {
      const source = roles[sourceCode];
      roleDrawerEyebrow.textContent = "COPY ROLE";
      roleDrawerTitle.textContent = `复制角色 · ${source.name}`;
      saveRoleFormButton.textContent = "创建副本";
      roleNameInput.value = `${source.name}副本`;
      roleCodeInput.value = `${source.code}_COPY`;
      roleDescriptionInput.value = `基于“${source.name}”复制的自定义角色。`;
      setCopyFromValue(sourceCode);
    }

    if (mode === "edit") {
      const role = roles[sourceCode];
      roleDrawerEyebrow.textContent = "EDIT ROLE";
      roleDrawerTitle.textContent = `编辑角色 · ${role.name}`;
      saveRoleFormButton.textContent = "保存角色信息";
      roleNameInput.value = role.name;
      roleCodeInput.value = role.code;
      roleDescriptionInput.value = role.description;
      roleEnabledInput.checked = role.status === "ACTIVE";
      setCopyFromValue("");
      if (role.isSystem) roleNameInput.disabled = true;
    }

    closeMenus();
    roleDrawer.hidden = false;
    setOverlayState();
    window.setTimeout(() => {
      (roleNameInput.disabled ? roleDescriptionInput : roleNameInput).focus();
    }, 0);
  };

  const closeRoleDrawer = () => {
    if (roleDrawer.hidden) return;
    roleDrawer.hidden = true;
    closeMenus();
    setOverlayState();
    previousFocus?.focus();
  };

  addRoleButton.addEventListener("click", () => openRoleDrawer("create"));
  copyRoleButton.addEventListener("click", () => openRoleDrawer("copy", currentRoleCode));

  const copyFromTrigger = copyFromSelect.querySelector(".role-form-select-trigger");
  const copyFromMenu = copyFromSelect.querySelector(".role-form-select-menu");

  copyFromTrigger.addEventListener("click", (event) => {
    const willOpen = copyFromMenu.hidden;
    closeMenus(willOpen ? copyFromMenu : null);
    copyFromMenu.hidden = !willOpen;
    copyFromTrigger.setAttribute("aria-expanded", String(willOpen));
    if (willOpen) copyFromMenu.querySelector(".is-selected")?.focus();
    event.stopPropagation();
  });

  copyFromMenu.addEventListener("click", (event) => {
    const option = event.target.closest("[data-value]");
    if (!option) return;
    setCopyFromValue(option.dataset.value);
    copyFromMenu.hidden = true;
    copyFromTrigger.setAttribute("aria-expanded", "false");
    copyFromTrigger.focus();
    event.stopPropagation();
  });

  const validateRoleForm = () => {
    clearRoleFormErrors();
    let valid = true;
    const name = roleNameInput.value.trim();
    const code = roleCodeInput.value.trim().toUpperCase();
    if (!name) {
      setRoleFormError(roleNameInput, "请输入角色名称");
      valid = false;
    }
    if (!code) {
      setRoleFormError(roleCodeInput, "请输入角色编码");
      valid = false;
    } else if (!/^[A-Z][A-Z0-9_]{2,63}$/.test(code)) {
      setRoleFormError(roleCodeInput, "编码需以大写字母开头，仅包含大写字母、数字和下划线");
      valid = false;
    } else if (drawerMode !== "edit" && roles[code]) {
      setRoleFormError(roleCodeInput, "该角色编码已存在");
      valid = false;
    }
    return valid;
  };

  roleNameInput.addEventListener("input", clearRoleFormErrors);
  roleCodeInput.addEventListener("input", () => {
    roleCodeInput.value = roleCodeInput.value.toUpperCase().replace(/[^A-Z0-9_]/g, "");
    clearRoleFormErrors();
  });

  roleForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!validateRoleForm()) return;

    if (drawerMode === "edit") {
      const role = roles[drawerTargetCode];
      role.name = roleNameInput.value.trim();
      role.description =
        roleDescriptionInput.value.trim() || "未填写角色说明。";
      role.status = roleEnabledInput.checked ? "ACTIVE" : "DISABLED";
      updateRoleListMeta(role);
      closeRoleDrawer();
      renderRole();
      showToast(`“${role.name}”的角色信息已更新。`);
      return;
    }

    const code = roleCodeInput.value.trim().toUpperCase();
    const source = copyFromInput.value ? roles[copyFromInput.value] : null;
    const copiedPermissions = source ? [...source.permissions] : ["dashboard.view"];
    const copiedScope =
      source && source.scope !== "ALL" ? source.scope : "ASSIGNED";
    const role = createRole({
      code,
      name: roleNameInput.value.trim(),
      description:
        roleDescriptionInput.value.trim() || "自定义角色，权限需由系统管理员配置。",
      users: 0,
      permissions: copiedPermissions,
      scope: copiedScope,
      allowedScopes: ["OWNED", "ASSIGNED", "OWNED_OR_ASSIGNED", "NONE"],
      isSystem: false,
      status: roleEnabledInput.checked ? "ACTIVE" : "DISABLED",
      updated: formatDateTime(new Date()),
      iconClass: "role-icon-custom"
    });
    enforceRoleBoundaries(role);
    role.savedPermissions = new Set(role.permissions);
    role.defaultPermissions = new Set(role.permissions);
    role.savedScope = role.scope;
    role.defaultScope = role.scope;
    roles[code] = role;
    roleList.append(createRoleListItem(role));
    currentRoleCode = code;
    directoryCount.textContent = String(Object.keys(roles).length);
    roleTotalCount.innerHTML = `${Object.keys(roles).length}<em>个</em>`;
    closeRoleDrawer();
    renderRole();
    showToast(
      `自定义角色“${role.name}”已创建，系统专属权限已按边界自动移除。`
    );
  });

  const openConfirm = ({ title, copy, tip, label, danger = false, action }) => {
    previousFocus = document.activeElement;
    confirmTitle.textContent = title;
    confirmCopy.textContent = copy;
    confirmTip.textContent = tip;
    confirmButton.textContent = label;
    confirmButton.classList.toggle("is-danger", danger);
    pendingConfirmAction = action;
    confirmModal.hidden = false;
    setOverlayState();
    confirmButton.focus();
  };

  const closeConfirm = () => {
    if (confirmModal.hidden) return;
    confirmModal.hidden = true;
    pendingConfirmAction = null;
    setOverlayState();
    previousFocus?.focus();
  };

  confirmButton.addEventListener("click", () => {
    const action = pendingConfirmAction;
    confirmModal.hidden = true;
    pendingConfirmAction = null;
    setOverlayState();
    action?.();
  });

  roleMoreButton.addEventListener("click", (event) => {
    const willOpen = roleMoreMenu.hidden;
    closeMenus(willOpen ? roleMoreMenu : null);
    roleMoreMenu.hidden = !willOpen;
    roleMoreButton.setAttribute("aria-expanded", String(willOpen));
    event.stopPropagation();
  });

  roleMoreMenu.addEventListener("click", (event) => {
    const actionButton = event.target.closest("[data-role-action]");
    if (!actionButton || actionButton.disabled) return;
    const action = actionButton.dataset.roleAction;
    const role = currentRole();
    closeMenus();

    if (action === "edit") openRoleDrawer("edit", role.code);
    if (action === "copy") openRoleDrawer("copy", role.code);
    if (action === "toggle-status") {
      const disabling = role.status === "ACTIVE";
      openConfirm({
        title: disabling ? "确认停用角色" : "确认启用角色",
        copy: disabling
          ? `停用“${role.name}”后，该角色不能再分配给新用户。`
          : `确定恢复“${role.name}”角色吗？`,
        tip: disabling
          ? `当前仍有${role.users}名用户关联此角色，停用前建议先调整用户角色；已有会话的权限将在下次请求时重新校验。`
          : "启用后，已关联用户将按该角色当前权限恢复访问。",
        label: disabling ? "确认停用" : "确认启用",
        danger: disabling,
        action: () => {
          role.status = disabling ? "DISABLED" : "ACTIVE";
          renderRole();
          showToast(`“${role.name}”已${disabling ? "停用" : "启用"}。`);
        }
      });
    }
    if (action === "delete") {
      if (role.isSystem) {
        showToast("系统预置角色不可删除。");
        return;
      }
      if (role.users > 0) {
        showToast("该角色仍有关联用户，请先调整用户角色后再删除。");
        return;
      }
      openConfirm({
        title: "删除自定义角色",
        copy: `确定删除“${role.name}”吗？`,
        tip: "角色删除后不可恢复，但历史授权和变更记录仍会保留在审计日志中。",
        label: "确认删除",
        danger: true,
        action: () => {
          delete roles[role.code];
          roleList.querySelector(`[data-role-code="${role.code}"]`)?.remove();
          currentRoleCode = "SYSTEM_ADMIN";
          directoryCount.textContent = String(Object.keys(roles).length);
          roleTotalCount.innerHTML = `${Object.keys(roles).length}<em>个</em>`;
          renderRole();
          showToast(`自定义角色“${role.name}”已删除。`);
        }
      });
    }
  });

  viewRoleUsersButton.addEventListener("click", () => {
    if (currentRole().users === 0) {
      showToast(`“${currentRole().name}”当前尚未关联用户。`);
      return;
    }
    window.location.href = `04-user-management.html?role=${encodeURIComponent(currentRoleCode)}`;
  });

  const renderMatrix = () => {
    const baseRoleCodes = [
      "SYSTEM_ADMIN",
      "RISK_ADMIN",
      "PROJECT_MANAGER",
      "VIEWER_AUDITOR"
    ];
    matrixTableBody.replaceChildren(
      ...permissionDefinitions.map((definition) => {
        const row = document.createElement("tr");
        const nameCell = document.createElement("td");
        const nameWrap = document.createElement("span");
        nameWrap.className = "matrix-permission-name";
        const name = document.createElement("strong");
        name.textContent = definition.name;
        const code = document.createElement("code");
        code.textContent = definition.key;
        nameWrap.append(name, code);
        nameCell.append(nameWrap);
        row.append(nameCell);

        baseRoleCodes.forEach((roleCode) => {
          const cell = document.createElement("td");
          const boundary = permissionBoundary(roleCode, definition.key);
          const mark = document.createElement("span");
          if (boundary.disabled) {
            mark.className = "matrix-cell-locked";
            mark.title = `${boundary.label}：${roles[roleCode].permissions.has(definition.key) ? "已授权" : "未授权"}`;
          } else if (roles[roleCode].permissions.has(definition.key)) {
            mark.className = "matrix-cell-check";
            mark.title = "已授权";
          } else {
            mark.className = "matrix-cell-empty";
            mark.title = "未授权";
          }
          cell.append(mark);
          row.append(cell);
        });
        return row;
      })
    );
  };

  const openMatrix = () => {
    previousFocus = document.activeElement;
    renderMatrix();
    matrixModal.hidden = false;
    setOverlayState();
    matrixModal.querySelector(".role-drawer-close").focus();
  };

  const closeMatrix = () => {
    if (matrixModal.hidden) return;
    matrixModal.hidden = true;
    setOverlayState();
    previousFocus?.focus();
  };

  permissionMatrixButton.addEventListener("click", openMatrix);

  document.querySelectorAll("[data-close-role-drawer]").forEach((button) => {
    button.addEventListener("click", closeRoleDrawer);
  });

  document.querySelectorAll("[data-close-matrix]").forEach((button) => {
    button.addEventListener("click", closeMatrix);
  });

  document.querySelectorAll("[data-close-role-confirm]").forEach((button) => {
    button.addEventListener("click", closeConfirm);
  });

  sidebarToggle.addEventListener("click", () => {
    const isOpen = sidebar.classList.toggle("is-open");
    sidebarToggle.setAttribute("aria-expanded", String(isOpen));
    sidebarBackdrop.hidden = !isOpen;
  });

  sidebarBackdrop.addEventListener("click", closeSidebar);

  profileButton.addEventListener("click", (event) => {
    const willOpen = profileMenu.hidden;
    profileMenu.hidden = !willOpen;
    profileButton.setAttribute("aria-expanded", String(willOpen));
    event.stopPropagation();
  });

  document.querySelector("[data-profile-action='security']")?.addEventListener("click", () => {
    profileMenu.hidden = true;
    profileButton.setAttribute("aria-expanded", "false");
    showToast("账号安全：当前会话权限正常，最近无异常登录。");
  });

  noticeButton.addEventListener("click", () => {
    showToast("权限提醒：1个自定义角色草稿待确认，1次范围变更已生效。");
  });

  document.querySelectorAll("[data-next-module]").forEach((button) => {
    button.addEventListener("click", () => {
      showToast(`${button.dataset.nextModule}将在后续页面中逐页生成。`);
      if (window.innerWidth <= 1040) closeSidebar();
    });
  });

  document.addEventListener("click", (event) => {
    closeMenus();
    if (!profileMenu.hidden && !profileMenu.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!confirmModal.hidden) {
      closeConfirm();
      return;
    }
    if (!matrixModal.hidden) {
      closeMatrix();
      return;
    }
    if (!roleDrawer.hidden) {
      closeRoleDrawer();
      return;
    }
    if (!profileMenu.hidden) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
      profileButton.focus();
      return;
    }
    closeMenus();
    closeSidebar();
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 1040) closeSidebar();
  });

  renderRole();
})();
