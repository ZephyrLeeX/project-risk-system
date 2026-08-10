(() => {
  const body = document.body;
  const sidebar = document.querySelector("#adminSidebar");
  const sidebarToggle = document.querySelector("#sidebarToggle");
  const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
  const profileButton = document.querySelector("#adminProfileButton");
  const profileMenu = document.querySelector("#adminProfileMenu");
  const noticeButton = document.querySelector("#noticeButton");
  const refreshButton = document.querySelector("#refreshAdminButton");
  const updatedAt = document.querySelector("#systemUpdatedAt");
  const moduleModal = document.querySelector("#moduleModal");
  const batchModal = document.querySelector("#batchModal");
  const moduleTitle = document.querySelector("#moduleModalTitle");
  const moduleDescription = document.querySelector("#moduleModalDescription");
  const moduleRole = document.querySelector("#moduleModalRole");
  const moduleScope = document.querySelector("#moduleModalScope");
  const moduleStatus = document.querySelector("#moduleModalStatus");
  const moduleCapabilities = document.querySelector("#moduleModalCapabilities");
  const moduleNotice = document.querySelector("#moduleModalNotice");
  const modulePrimaryAction = document.querySelector("#modulePrimaryAction");
  const toast = document.querySelector("#adminToast");
  const toastCopy = toast?.querySelector("p");
  let activeModule = "";
  let toastTimer;
  let previousFocus = null;

  const moduleData = {
    users: {
      title: "用户管理",
      url: "04-user-management.html",
      description: "统一维护平台账号、人员信息、启停状态，以及每个用户可查看的项目数据范围。",
      role: "系统管理员",
      scope: "全部项目",
      status: "32名用户，29名启用",
      capabilities: [
        "新增、编辑与停用用户账号",
        "分配角色与所属部门",
        "配置本人负责或被授权项目",
        "重置密码与查看登录状态"
      ],
      notice: "系统管理员维护账号与访问范围；个人邮箱配置属于风险管理员的个人能力，不在用户管理中代为配置。"
    },
    roles: {
      title: "角色权限",
      url: "05-role-permissions.html",
      description: "围绕系统管理员、风险管理员、项目经理和查看/审计员四类角色，配置菜单、操作和数据权限。",
      role: "系统管理员",
      scope: "角色级数据范围",
      status: "4个角色，46个权限点",
      capabilities: [
        "维护角色基本信息",
        "配置菜单与按钮权限",
        "设置项目数据范围",
        "查看角色关联用户"
      ],
      notice: "角色能力沿用既定业务边界：风险管理员负责审核和治理风险，项目经理负责所辖项目，查看/审计员仅可查看。"
    },
    imports: {
      title: "项目数据导入",
      url: "06-project-import.html",
      description: "通过 Excel 导入项目清单和回款数据，完成字段映射、格式校验、匹配确认与批次发布。",
      role: "系统管理员",
      scope: "全部项目",
      status: "最新批次有2条待确认",
      capabilities: [
        "下载并使用标准导入模板",
        "上传项目清单与回款数据",
        "校验字段、金额与项目匹配",
        "确认差异并发布数据批次"
      ],
      notice: "导入不会直接覆盖业务数据；只有校验通过并确认发布后，项目清单与回款数据才会更新。"
    },
    apikey: {
      title: "API Key 管理",
      url: "07-api-key-management.html",
      description: "维护 AI 服务的接入地址、模型、凭据有效期、默认服务和连通性状态。",
      role: "系统管理员",
      scope: "系统级配置",
      status: "2项服务，1个Key即将到期",
      capabilities: [
        "新增与停用 AI 服务配置",
        "加密保存并掩码展示 API Key",
        "配置模型、地址和默认服务",
        "执行连通性与权限测试"
      ],
      notice: "页面仅展示凭据尾号和有效期，完整 API Key 不回显；修改与测试操作都会写入审计日志。"
    },
    configs: {
      title: "系统配置",
      url: "08-system-config.html",
      description: "维护平台级基础参数，包括风险等级、风险分类、通知规则和业务字典。",
      role: "系统管理员",
      scope: "全系统",
      status: "18项配置生效中",
      capabilities: [
        "维护风险等级与颜色规则",
        "维护风险分类字典",
        "配置通知与提醒策略",
        "管理通用业务参数"
      ],
      notice: "影响既有风险数据解释的配置，需保留历史值并记录生效时间，避免破坏历史审计口径。"
    },
    audit: {
      title: "审计日志",
      url: "09-audit-logs.html",
      description: "记录账号登录、权限变更、数据导入、API Key 和系统配置等关键操作。",
      role: "系统管理员、查看/审计员",
      scope: "按授权范围查看",
      status: "今日已记录46条",
      capabilities: [
        "按人员、模块和时间检索",
        "查看操作前后变更内容",
        "追踪数据导入与发布记录",
        "导出授权范围内的审计结果"
      ],
      notice: "审计日志只允许查询和导出，不提供业务修改入口，关键记录需保证不可篡改和可追溯。"
    }
  };

  const pad = (value) => String(value).padStart(2, "0");

  const formatDateTime = (date) => {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };

  const showToast = (message) => {
    if (!toast || !toastCopy) return;
    window.clearTimeout(toastTimer);
    toastCopy.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 2800);
  };

  const setBodyModalState = () => {
    const hasOpenModal = !moduleModal.hidden || !batchModal.hidden;
    body.classList.toggle("has-admin-modal", hasOpenModal);
  };

  const renderModule = (moduleId) => {
    const data = moduleData[moduleId];
    if (!data) return;
    activeModule = moduleId;
    previousFocus = document.activeElement;
    moduleTitle.textContent = data.title;
    moduleDescription.textContent = data.description;
    moduleRole.textContent = data.role;
    moduleScope.textContent = data.scope;
    moduleStatus.textContent = data.status;
    moduleNotice.textContent = data.notice;
    moduleCapabilities.replaceChildren(
      ...data.capabilities.map((capability) => {
        const item = document.createElement("li");
        item.textContent = capability;
        return item;
      })
    );
    modulePrimaryAction.textContent = `进入${data.title}`;
    moduleModal.hidden = false;
    setBodyModalState();
    moduleModal.querySelector(".admin-modal-close")?.focus();
  };

  const closeModuleModal = () => {
    if (moduleModal.hidden) return;
    moduleModal.hidden = true;
    setBodyModalState();
    previousFocus?.focus();
  };

  const openBatchModal = (row) => {
    const batchId = row.dataset.batch;
    const fileName = row.querySelector(".file-cell strong")?.textContent ?? "项目清单.xlsx";
    const statusText = row.querySelector(".status-tag")?.textContent ?? "已发布";
    previousFocus = document.activeElement;
    document.querySelector("#batchFileName").textContent = fileName;
    document.querySelector("#batchNumber").textContent = batchId;
    const status = document.querySelector("#batchStatus");
    status.textContent = statusText;
    status.className = statusText === "已发布" ? "status-tag is-success" : "status-tag is-warning";
    batchModal.hidden = false;
    setBodyModalState();
    batchModal.querySelector(".admin-modal-close")?.focus();
  };

  const closeBatchModal = () => {
    if (batchModal.hidden) return;
    batchModal.hidden = true;
    setBodyModalState();
    previousFocus?.focus();
  };

  const closeSidebar = () => {
    sidebar.classList.remove("is-open");
    sidebarToggle.setAttribute("aria-expanded", "false");
    sidebarBackdrop.hidden = true;
  };

  sidebarToggle?.addEventListener("click", () => {
    const isOpen = sidebar.classList.toggle("is-open");
    sidebarToggle.setAttribute("aria-expanded", String(isOpen));
    sidebarBackdrop.hidden = !isOpen;
  });

  sidebarBackdrop?.addEventListener("click", closeSidebar);

  document.querySelectorAll("[data-module]").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (button.closest("#batchModal")) {
        closeBatchModal();
      }
      renderModule(button.dataset.module);
      if (window.innerWidth <= 1040) closeSidebar();
      event.stopPropagation();
    });
  });

  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", closeModuleModal);
  });

  document.querySelectorAll("[data-close-batch]").forEach((button) => {
    button.addEventListener("click", closeBatchModal);
  });

  document.querySelectorAll("[data-batch]").forEach((row) => {
    row.addEventListener("click", () => openBatchModal(row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openBatchModal(row);
      }
    });
  });

  modulePrimaryAction?.addEventListener("click", () => {
    const data = moduleData[activeModule];
    closeModuleModal();
    if (data.url) {
      window.location.href = data.url;
      return;
    }
    showToast(`${data.title}已选中，后续将按页面顺序进入详细管理界面。`);
  });

  profileButton?.addEventListener("click", (event) => {
    const willOpen = profileMenu.hidden;
    profileMenu.hidden = !willOpen;
    profileButton.setAttribute("aria-expanded", String(willOpen));
    event.stopPropagation();
  });

  document.querySelector("[data-profile-action='security']")?.addEventListener("click", () => {
    profileMenu.hidden = true;
    profileButton.setAttribute("aria-expanded", "false");
    showToast("账号安全：最近一次登录为今天 08:31，当前无异常登录。");
  });

  document.addEventListener("click", (event) => {
    if (!profileMenu.hidden && !profileMenu.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
  });

  noticeButton?.addEventListener("click", () => {
    showToast("3项后台提醒：2条数据待确认、1个 API Key 即将到期。");
  });

  refreshButton?.addEventListener("click", () => {
    if (refreshButton.classList.contains("is-loading")) return;
    refreshButton.classList.add("is-loading");
    refreshButton.disabled = true;
    window.setTimeout(() => {
      const now = new Date();
      updatedAt.textContent = formatDateTime(now);
      refreshButton.classList.remove("is-loading");
      refreshButton.disabled = false;
      showToast("系统运行状态已刷新，全部核心服务正常。");
    }, 650);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!batchModal.hidden) {
      closeBatchModal();
      return;
    }
    if (!moduleModal.hidden) {
      closeModuleModal();
      return;
    }
    if (!profileMenu.hidden) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
      profileButton.focus();
      return;
    }
    closeSidebar();
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 1040) closeSidebar();
  });
})();
