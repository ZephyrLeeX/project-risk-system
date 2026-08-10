(() => {
  const body = document.body;
  const sidebar = document.querySelector("#adminSidebar");
  const sidebarToggle = document.querySelector("#sidebarToggle");
  const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
  const profileButton = document.querySelector("#adminProfileButton");
  const profileMenu = document.querySelector("#adminProfileMenu");
  const noticeButton = document.querySelector("#noticeButton");
  const addUserButton = document.querySelector("#addUserButton");
  const userSearch = document.querySelector("#userSearch");
  const resetFiltersButton = document.querySelector("#resetUserFilters");
  const tableBody = document.querySelector("#userTableBody");
  const selectAll = document.querySelector("#selectAllUsers");
  const listCount = document.querySelector("#listCount");
  const emptyUsers = document.querySelector("#emptyUsers");
  const bulkActionBar = document.querySelector("#bulkActionBar");
  const selectedCount = document.querySelector("#selectedCount");
  const userDrawer = document.querySelector("#userDrawer");
  const userForm = document.querySelector("#userForm");
  const drawerTitle = document.querySelector("#userDrawerTitle");
  const drawerEyebrow = document.querySelector("#drawerEyebrow");
  const displayNameInput = document.querySelector("#userDisplayName");
  const usernameInput = document.querySelector("#userUsername");
  const departmentInput = document.querySelector("#userDepartment");
  const enabledInput = document.querySelector("#userEnabled");
  const initialPasswordNote = document.querySelector("#initialPasswordNote");
  const saveUserButton = document.querySelector("#saveUserButton");
  const roleError = document.querySelector("#roleError");
  const scopeError = document.querySelector("#scopeError");
  const assignedProjects = document.querySelector("#assignedProjects");
  const confirmModal = document.querySelector("#confirmModal");
  const confirmTitle = document.querySelector("#confirmModalTitle");
  const confirmCopy = document.querySelector("#confirmModalCopy");
  const confirmTip = document.querySelector("#confirmModalTip");
  const confirmButton = document.querySelector("#confirmActionButton");
  const recordsModal = document.querySelector("#recordsModal");
  const recordsUserName = document.querySelector("#recordsUserName");
  const recordsUsername = document.querySelector("#recordsUsername");
  const recordsAvatar = document.querySelector("#recordsAvatar");
  const recordsStatus = document.querySelector("#recordsStatus");
  const toast = document.querySelector("#usersToast");
  const toastCopy = toast?.querySelector("p");

  const roleNames = {
    SYSTEM_ADMIN: "系统管理员",
    RISK_ADMIN: "风险管理员",
    PROJECT_MANAGER: "项目经理",
    VIEWER_AUDITOR: "查看/审计员"
  };

  const roleClasses = {
    SYSTEM_ADMIN: "role-admin",
    RISK_ADMIN: "role-risk",
    PROJECT_MANAGER: "role-manager",
    VIEWER_AUDITOR: "role-auditor"
  };

  const defaultScopes = {
    SYSTEM_ADMIN: "ALL",
    RISK_ADMIN: "ALL",
    PROJECT_MANAGER: "OWNED_OR_ASSIGNED",
    VIEWER_AUDITOR: "ASSIGNED"
  };

  const scopeNames = {
    ALL: "全部项目",
    OWNED_OR_ASSIGNED: "本人负责及授权",
    OWNED: "本人负责项目",
    ASSIGNED: "被授权项目",
    NONE: "无项目数据"
  };

  const filters = {
    role: "all",
    status: "all",
    department: "all"
  };

  let totalUsers = 32;
  let activeRow = null;
  let drawerMode = "create";
  let pendingConfirmAction = null;
  let previousFocus = null;
  let toastTimer = null;
  let loadingForm = false;

  const getRows = () => [...tableBody.querySelectorAll("tr")];

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
    const hasOverlay = !userDrawer.hidden || !confirmModal.hidden || !recordsModal.hidden;
    body.classList.toggle("users-overlay-open", hasOverlay);
  };

  const closeAllMenus = (except = null) => {
    document.querySelectorAll(".filter-select-menu, .form-select-menu, .row-action-menu").forEach((menu) => {
      if (menu === except) return;
      menu.hidden = true;
      const trigger = menu.parentElement.querySelector(
        ".filter-select-trigger, .form-select-trigger, .more-user-button"
      );
      trigger?.setAttribute("aria-expanded", "false");
    });
  };

  const closeSidebar = () => {
    sidebar.classList.remove("is-open");
    sidebarToggle.setAttribute("aria-expanded", "false");
    sidebarBackdrop.hidden = true;
  };

  const updateSelectionState = () => {
    const visibleRows = getRows().filter((row) => !row.hidden);
    const checkedRows = visibleRows.filter((row) => row.querySelector(".row-checkbox").checked);
    const allChecked = visibleRows.length > 0 && checkedRows.length === visibleRows.length;
    const someChecked = checkedRows.length > 0 && !allChecked;

    selectAll.checked = allChecked;
    selectAll.indeterminate = someChecked;
    selectedCount.textContent = String(checkedRows.length);
    bulkActionBar.hidden = checkedRows.length === 0;

    getRows().forEach((row) => {
      row.classList.toggle("is-checked", row.querySelector(".row-checkbox").checked);
    });
  };

  const applyFilters = () => {
    const keyword = userSearch.value.trim().toLowerCase();
    let visibleCount = 0;

    getRows().forEach((row) => {
      const searchText = [
        row.dataset.name,
        row.dataset.username,
        row.dataset.department
      ].join(" ").toLowerCase();
      const matchesKeyword = !keyword || searchText.includes(keyword);
      const matchesRole = filters.role === "all" || row.dataset.role === filters.role;
      const matchesStatus = filters.status === "all" || row.dataset.status === filters.status;
      const matchesDepartment =
        filters.department === "all" || row.dataset.department === filters.department;
      const visible = matchesKeyword && matchesRole && matchesStatus && matchesDepartment;
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });

    listCount.textContent =
      visibleCount === getRows().length && !keyword && Object.values(filters).every((value) => value === "all")
        ? `共${totalUsers}名用户`
        : `当前显示${visibleCount}名用户`;
    emptyUsers.hidden = visibleCount !== 0;
    document.querySelector(".users-table").hidden = visibleCount === 0;
    updateSelectionState();
  };

  const resetFilters = () => {
    userSearch.value = "";
    Object.keys(filters).forEach((key) => {
      filters[key] = "all";
      const container = document.querySelector(`[data-filter="${key}"]`);
      const firstOption = container.querySelector("[data-value='all']");
      container.querySelector(".filter-select-trigger span").textContent = firstOption.textContent;
      container.querySelectorAll("[role='option']").forEach((option) => {
        const selected = option === firstOption;
        option.classList.toggle("is-selected", selected);
        option.setAttribute("aria-selected", String(selected));
      });
    });
    document.querySelectorAll("[data-summary-filter]").forEach((card) => {
      card.classList.remove("is-filtering");
    });
    closeAllMenus();
    applyFilters();
  };

  const selectFilterOption = (container, option) => {
    const filterName = container.dataset.filter;
    filters[filterName] = option.dataset.value;
    container.querySelector(".filter-select-trigger span").textContent = option.textContent;
    container.querySelectorAll("[role='option']").forEach((item) => {
      const selected = item === option;
      item.classList.toggle("is-selected", selected);
      item.setAttribute("aria-selected", String(selected));
    });
    container.querySelector(".filter-select-menu").hidden = true;
    container.querySelector(".filter-select-trigger").setAttribute("aria-expanded", "false");
    document.querySelectorAll("[data-summary-filter]").forEach((card) => {
      card.classList.toggle(
        "is-filtering",
        filterName === "status" && card.dataset.summaryFilter === option.dataset.value
      );
    });
    applyFilters();
  };

  document.querySelectorAll(".filter-select").forEach((container) => {
    const trigger = container.querySelector(".filter-select-trigger");
    const menu = container.querySelector(".filter-select-menu");

    trigger.addEventListener("click", (event) => {
      const willOpen = menu.hidden;
      closeAllMenus(willOpen ? menu : null);
      menu.hidden = !willOpen;
      trigger.setAttribute("aria-expanded", String(willOpen));
      if (willOpen) {
        menu.querySelector(".is-selected")?.focus();
      }
      event.stopPropagation();
    });

    menu.addEventListener("click", (event) => {
      const option = event.target.closest("[data-value]");
      if (!option) return;
      selectFilterOption(container, option);
      trigger.focus();
      event.stopPropagation();
    });

    menu.addEventListener("keydown", (event) => {
      const options = [...menu.querySelectorAll("[data-value]")];
      const currentIndex = options.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        options[(currentIndex + direction + options.length) % options.length].focus();
      }
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectFilterOption(container, document.activeElement);
        trigger.focus();
      }
      if (event.key === "Escape") {
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        trigger.focus();
      }
    });
  });

  document.querySelectorAll("[data-summary-filter]").forEach((card) => {
    card.addEventListener("click", () => {
      const status = card.dataset.summaryFilter;
      const statusFilter = document.querySelector("[data-filter='status']");
      const option = statusFilter.querySelector(`[data-value="${status}"]`);
      selectFilterOption(statusFilter, option);
      document.querySelectorAll("[data-summary-filter]").forEach((item) => {
        item.classList.toggle("is-filtering", item === card && status !== "all");
      });
    });
  });

  const setFormDepartment = (value) => {
    const container = document.querySelector("[data-form-select='department']");
    const trigger = container.querySelector(".form-select-trigger");
    const label = value || "请选择所属部门";
    departmentInput.value = value;
    trigger.querySelector("span").textContent = label;
    trigger.classList.toggle("has-value", Boolean(value));
    container.querySelectorAll("[data-value]").forEach((option) => {
      option.classList.toggle("is-selected", option.dataset.value === value);
    });
  };

  const clearFieldError = (field) => {
    field.classList.remove("is-invalid");
    const error = field.querySelector(".field-error");
    if (error) error.textContent = "";
  };

  const setFieldError = (field, message) => {
    field.classList.add("is-invalid");
    const error = field.querySelector(".field-error");
    if (error) error.textContent = message;
  };

  const getSelectedRole = () => userForm.querySelector("input[name='role']:checked")?.value ?? "";
  const getSelectedScope = () => userForm.querySelector("input[name='scope']:checked")?.value ?? "";

  const updateAssignedProjectsVisibility = () => {
    const scope = getSelectedScope();
    assignedProjects.hidden = !["ASSIGNED", "OWNED_OR_ASSIGNED"].includes(scope);
  };

  const setSelectedRole = (role) => {
    userForm.querySelectorAll("input[name='role']").forEach((input) => {
      input.checked = input.value === role;
    });
  };

  const setSelectedScope = (scope) => {
    userForm.querySelectorAll("input[name='scope']").forEach((input) => {
      input.checked = input.value === scope;
    });
    updateAssignedProjectsVisibility();
  };

  const resetFormValidation = () => {
    userForm.querySelectorAll(".form-field").forEach(clearFieldError);
    roleError.textContent = "";
    scopeError.textContent = "";
  };

  const openDrawer = (row = null) => {
    previousFocus = document.activeElement;
    activeRow = row;
    drawerMode = row ? "edit" : "create";
    loadingForm = true;
    userForm.reset();
    resetFormValidation();

    if (row) {
      drawerEyebrow.textContent = "EDIT USER";
      drawerTitle.textContent = `编辑用户 · ${row.dataset.name}`;
      saveUserButton.textContent = "保存修改";
      initialPasswordNote.hidden = true;
      displayNameInput.value = row.dataset.name;
      usernameInput.value = row.dataset.username;
      usernameInput.disabled = true;
      setFormDepartment(row.dataset.department);
      setSelectedRole(row.dataset.role);
      setSelectedScope(row.dataset.scope);
      enabledInput.checked = row.dataset.status !== "DISABLED";
    } else {
      drawerEyebrow.textContent = "CREATE USER";
      drawerTitle.textContent = "新增用户";
      saveUserButton.textContent = "创建用户";
      initialPasswordNote.hidden = false;
      usernameInput.disabled = false;
      setFormDepartment("");
      setSelectedRole("");
      setSelectedScope("");
      enabledInput.checked = true;
    }

    loadingForm = false;
    closeAllMenus();
    userDrawer.hidden = false;
    setOverlayState();
    window.setTimeout(() => displayNameInput.focus(), 0);
  };

  const closeDrawer = () => {
    if (userDrawer.hidden) return;
    userDrawer.hidden = true;
    closeAllMenus();
    setOverlayState();
    previousFocus?.focus();
  };

  document.querySelectorAll(".form-custom-select").forEach((container) => {
    const trigger = container.querySelector(".form-select-trigger");
    const menu = container.querySelector(".form-select-menu");

    trigger.addEventListener("click", (event) => {
      const willOpen = menu.hidden;
      closeAllMenus(willOpen ? menu : null);
      menu.hidden = !willOpen;
      trigger.setAttribute("aria-expanded", String(willOpen));
      if (willOpen) menu.querySelector(".is-selected, [data-value]")?.focus();
      event.stopPropagation();
    });

    menu.addEventListener("click", (event) => {
      const option = event.target.closest("[data-value]");
      if (!option) return;
      setFormDepartment(option.dataset.value);
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      clearFieldError(container.closest(".form-field"));
      trigger.focus();
      event.stopPropagation();
    });
  });

  userForm.querySelectorAll("input[name='role']").forEach((input) => {
    input.addEventListener("change", () => {
      roleError.textContent = "";
      if (!loadingForm) setSelectedScope(defaultScopes[input.value]);
    });
  });

  userForm.querySelectorAll("input[name='scope']").forEach((input) => {
    input.addEventListener("change", () => {
      scopeError.textContent = "";
      updateAssignedProjectsVisibility();
    });
  });

  displayNameInput.addEventListener("input", () => clearFieldError(displayNameInput.closest(".form-field")));
  usernameInput.addEventListener("input", () => clearFieldError(usernameInput.closest(".form-field")));

  const validateForm = () => {
    resetFormValidation();
    let valid = true;
    if (!displayNameInput.value.trim()) {
      setFieldError(displayNameInput.closest(".form-field"), "请输入用户姓名");
      valid = false;
    }
    if (!usernameInput.value.trim()) {
      setFieldError(usernameInput.closest(".form-field"), "请输入登录账号");
      valid = false;
    } else if (!/^[a-zA-Z][a-zA-Z0-9._-]{2,31}$/.test(usernameInput.value.trim())) {
      setFieldError(usernameInput.closest(".form-field"), "账号需以字母开头，长度为3–32位");
      valid = false;
    }
    if (!departmentInput.value) {
      setFieldError(departmentInput.closest(".form-field"), "请选择所属部门");
      valid = false;
    }
    if (!getSelectedRole()) {
      roleError.textContent = "请选择一个用户角色";
      valid = false;
    }
    if (!getSelectedScope()) {
      scopeError.textContent = "请选择项目数据范围";
      valid = false;
    }
    return valid;
  };

  const scopeCountCopy = (scope) => {
    if (scope === "ALL") return "131个项目";
    if (scope === "OWNED_OR_ASSIGNED") return "按负责与授权实时计算";
    if (scope === "ASSIGNED") return "4个项目";
    if (scope === "OWNED") return "按负责人实时计算";
    return "0个项目";
  };

  const statusMarkup = (status) => {
    if (status === "DISABLED") {
      return '<span class="account-status status-disabled"><i></i>停用</span>';
    }
    if (status === "LOCKED") {
      return '<span class="account-status status-locked"><i></i>锁定至10:30</span>';
    }
    return '<span class="account-status status-active"><i></i>启用</span>';
  };

  const updateRowFromForm = (row) => {
    const name = displayNameInput.value.trim();
    const username = usernameInput.value.trim();
    const role = getSelectedRole();
    const scope = getSelectedScope();
    const previousStatus = row.dataset.status;
    const status = enabledInput.checked
      ? previousStatus === "LOCKED" ? "LOCKED" : "ACTIVE"
      : "DISABLED";

    row.dataset.name = name;
    row.dataset.username = username;
    row.dataset.department = departmentInput.value;
    row.dataset.role = role;
    row.dataset.roleName = roleNames[role];
    row.dataset.scope = scope;
    row.dataset.scopeLabel = scopeNames[scope];
    row.dataset.status = status;

    const identity = row.cells[1].querySelector(".user-identity");
    identity.querySelector(".user-avatar").textContent = name.charAt(0);
    identity.querySelector("strong").textContent = name;
    identity.querySelector("small").textContent = username;
    row.querySelector(".row-checkbox").setAttribute("aria-label", `选择${name}`);
    row.cells[2].textContent = departmentInput.value;
    row.cells[3].innerHTML = `<span class="role-tag ${roleClasses[role]}">${roleNames[role]}</span>`;
    row.cells[4].innerHTML =
      `<span class="scope-cell"><strong>${scopeNames[scope]}</strong><small>${scopeCountCopy(scope)}</small></span>`;
    row.cells[5].innerHTML = statusMarkup(status);
  };

  const createNewRow = () => {
    const source = getRows()[0];
    const row = source.cloneNode(true);
    row.dataset.userId = `u-${String(totalUsers + 1).padStart(3, "0")}`;
    row.querySelector(".user-avatar").className = "user-avatar avatar-cyan";
    row.cells[6].innerHTML =
      '<span class="login-cell"><strong>尚未登录</strong><small>等待首次登录</small></span>';
    row.querySelectorAll(".row-action-menu button").forEach((button) => {
      if (button.dataset.rowAction === "disable") button.textContent = "停用账号";
    });
    row.querySelector(".row-checkbox").checked = false;
    updateRowFromForm(row);
    tableBody.prepend(row);
    totalUsers += 1;
    document.querySelector(".user-summary-card.is-total strong").innerHTML =
      `${totalUsers}<em>人</em>`;
    return row;
  };

  userForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!validateForm()) return;

    const duplicate = getRows().find(
      (row) =>
        row !== activeRow &&
        row.dataset.username.toLowerCase() === usernameInput.value.trim().toLowerCase()
    );
    if (duplicate) {
      setFieldError(usernameInput.closest(".form-field"), "该登录账号已存在");
      usernameInput.focus();
      return;
    }

    if (drawerMode === "edit") {
      updateRowFromForm(activeRow);
      closeDrawer();
      applyFilters();
      showToast(`用户“${activeRow.dataset.name}”的信息已更新，并记录到审计日志。`);
      return;
    }

    const newRow = createNewRow();
    closeDrawer();
    resetFilters();
    showToast(`用户“${newRow.dataset.name}”已创建，系统已生成一次性初始密码。`);
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

  const setRowStatus = (row, status) => {
    row.dataset.status = status;
    row.cells[5].innerHTML = statusMarkup(status);
    const menu = row.querySelector(".row-action-menu");
    const statusAction = menu.querySelector(
      "[data-row-action='disable'], [data-row-action='enable'], [data-row-action='unlock']"
    );
    if (statusAction) {
      statusAction.dataset.rowAction = status === "ACTIVE" ? "disable" : "enable";
      statusAction.textContent = status === "ACTIVE" ? "停用账号" : "启用账号";
    }
  };

  const confirmStatusChange = (row, nextStatus) => {
    const isDisabling = nextStatus === "DISABLED";
    if (row.dataset.userId === "u-001" && isDisabling) {
      showToast("不能停用当前登录的系统管理员账号。");
      return;
    }
    openConfirm({
      title: isDisabling ? "确认停用账号" : nextStatus === "ACTIVE" ? "确认启用账号" : "确认解除锁定",
      copy: isDisabling
        ? `停用“${row.dataset.name}”后，该用户将无法登录平台。`
        : `确定恢复“${row.dataset.name}”的正常登录状态吗？`,
      tip: isDisabling
        ? "停用操作会立即撤销该用户的全部登录会话，但不会删除历史数据和审计记录。"
        : "恢复后，用户仍按现有角色和项目数据范围访问平台。",
      label: isDisabling ? "确认停用" : "确认恢复",
      danger: isDisabling,
      action: () => {
        setRowStatus(row, nextStatus);
        applyFilters();
        showToast(`用户“${row.dataset.name}”已${isDisabling ? "停用" : "恢复启用"}。`);
      }
    });
  };

  const confirmPasswordReset = (row) => {
    openConfirm({
      title: "重置用户密码",
      copy: `确定重置“${row.dataset.name}”的登录密码吗？`,
      tip: "系统将生成一次性临时密码，撤销该用户全部会话，并要求下次登录时修改密码。",
      label: "确认重置",
      action: () => {
        showToast(`“${row.dataset.name}”的密码已重置，一次性密码已生成。`);
      }
    });
  };

  const openRecords = (row) => {
    previousFocus = document.activeElement;
    recordsUserName.textContent = row.dataset.name;
    recordsUsername.textContent = row.dataset.username;
    recordsAvatar.textContent = row.dataset.name.charAt(0);
    recordsStatus.className =
      row.dataset.status === "LOCKED"
        ? "account-status status-locked"
        : row.dataset.status === "DISABLED"
          ? "account-status status-disabled"
          : "account-status status-active";
    recordsStatus.innerHTML =
      row.dataset.status === "LOCKED"
        ? "<i></i>当前锁定"
        : row.dataset.status === "DISABLED"
          ? "<i></i>当前停用"
          : "<i></i>当前启用";
    recordsModal.hidden = false;
    setOverlayState();
    recordsModal.querySelector(".drawer-close").focus();
  };

  const closeRecords = () => {
    if (recordsModal.hidden) return;
    recordsModal.hidden = true;
    setOverlayState();
    previousFocus?.focus();
  };

  tableBody.addEventListener("click", (event) => {
    const row = event.target.closest("tr");
    if (!row) return;

    if (event.target.closest("[data-edit-user]")) {
      closeAllMenus();
      openDrawer(row);
      return;
    }

    const moreButton = event.target.closest("[data-more-user]");
    if (moreButton) {
      const menu = moreButton.nextElementSibling;
      const willOpen = menu.hidden;
      closeAllMenus(willOpen ? menu : null);
      menu.hidden = !willOpen;
      moreButton.setAttribute("aria-expanded", String(willOpen));
      event.stopPropagation();
      return;
    }

    const actionButton = event.target.closest("[data-row-action]");
    if (actionButton) {
      closeAllMenus();
      const action = actionButton.dataset.rowAction;
      if (action === "reset-password") confirmPasswordReset(row);
      if (action === "view-records") openRecords(row);
      if (action === "disable") confirmStatusChange(row, "DISABLED");
      if (action === "enable" || action === "unlock") confirmStatusChange(row, "ACTIVE");
      return;
    }

    if (event.target.closest(".row-checkbox")) {
      window.setTimeout(updateSelectionState, 0);
    }
  });

  selectAll.addEventListener("change", () => {
    getRows().filter((row) => !row.hidden).forEach((row) => {
      row.querySelector(".row-checkbox").checked = selectAll.checked;
    });
    updateSelectionState();
  });

  document.querySelectorAll("[data-bulk-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.bulkAction;
      if (action === "clear") {
        getRows().forEach((row) => {
          row.querySelector(".row-checkbox").checked = false;
        });
        updateSelectionState();
        return;
      }
      const selectedRows = getRows().filter((row) => row.querySelector(".row-checkbox").checked);
      const isDisabling = action === "disable";
      openConfirm({
        title: isDisabling ? "批量停用用户" : "批量启用用户",
        copy: `即将${isDisabling ? "停用" : "启用"}已选择的${selectedRows.length}名用户。`,
        tip: isDisabling
          ? "当前登录的系统管理员账号将自动跳过，其他用户的登录会话会立即撤销。"
          : "启用后，用户将按原角色与项目数据范围恢复访问。",
        label: isDisabling ? "确认停用" : "确认启用",
        danger: isDisabling,
        action: () => {
          let changed = 0;
          selectedRows.forEach((row) => {
            if (isDisabling && row.dataset.userId === "u-001") return;
            setRowStatus(row, isDisabling ? "DISABLED" : "ACTIVE");
            row.querySelector(".row-checkbox").checked = false;
            changed += 1;
          });
          applyFilters();
          showToast(`已${isDisabling ? "停用" : "启用"}${changed}名用户。`);
        }
      });
    });
  });

  confirmButton.addEventListener("click", () => {
    const action = pendingConfirmAction;
    confirmModal.hidden = true;
    pendingConfirmAction = null;
    setOverlayState();
    action?.();
  });

  addUserButton.addEventListener("click", () => openDrawer());

  document.querySelectorAll("[data-close-drawer]").forEach((button) => {
    button.addEventListener("click", closeDrawer);
  });

  document.querySelectorAll("[data-close-confirm]").forEach((button) => {
    button.addEventListener("click", closeConfirm);
  });

  document.querySelectorAll("[data-close-records]").forEach((button) => {
    button.addEventListener("click", closeRecords);
  });

  userSearch.addEventListener("input", applyFilters);
  resetFiltersButton.addEventListener("click", resetFilters);

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
    showToast("账号安全：最近一次登录为今天08:31，当前无异常登录。");
  });

  noticeButton.addEventListener("click", () => {
    showToast("后台提醒：1个账号临时锁定，2名停用用户待定期复核。");
  });

  document.querySelectorAll("[data-next-module]").forEach((button) => {
    button.addEventListener("click", () => {
      showToast(`${button.dataset.nextModule}将在后续页面中逐页生成。`);
      if (window.innerWidth <= 1040) closeSidebar();
    });
  });

  document.querySelectorAll(".users-pagination nav button:not(:disabled)").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.classList.contains("is-current")) return;
      showToast("原型已展示第1页用户，其他分页将在接入真实数据后加载。");
    });
  });

  document.querySelector(".add-project-chip")?.addEventListener("click", () => {
    showToast("项目授权选择器将在项目数据接入后展示131个有效项目。");
  });

  document.querySelectorAll(".project-chips button:not(.add-project-chip)").forEach((button) => {
    button.addEventListener("click", () => {
      button.remove();
      showToast("已从当前配置中移除该授权项目，保存后生效。");
    });
  });

  document.addEventListener("click", (event) => {
    closeAllMenus();
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
    if (!recordsModal.hidden) {
      closeRecords();
      return;
    }
    if (!userDrawer.hidden) {
      closeDrawer();
      return;
    }
    if (!profileMenu.hidden) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
      profileButton.focus();
      return;
    }
    closeAllMenus();
    closeSidebar();
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 1040) closeSidebar();
  });

  const initialRoleFilter = new URLSearchParams(window.location.search).get("role");
  const roleFilterContainer = document.querySelector("[data-filter='role']");
  const initialRoleOption = initialRoleFilter
    ? roleFilterContainer.querySelector(`[data-value="${initialRoleFilter}"]`)
    : null;
  if (initialRoleOption) {
    selectFilterOption(roleFilterContainer, initialRoleOption);
  } else {
    applyFilters();
  }
})();
