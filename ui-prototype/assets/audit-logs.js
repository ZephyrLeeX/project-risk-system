(() => {
  const body = document.body;
  const sidebar = document.querySelector("#adminSidebar");
  const sidebarToggle = document.querySelector("#sidebarToggle");
  const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
  const profileButton = document.querySelector("#adminProfileButton");
  const profileMenu = document.querySelector("#adminProfileMenu");
  const noticeButton = document.querySelector("#noticeButton");
  const auditSearch = document.querySelector("#auditSearch");
  const auditTableBody = document.querySelector("#auditTableBody");
  const auditEmpty = document.querySelector("#auditEmpty");
  const visibleAuditCount = document.querySelector("#visibleAuditCount");
  const activeFilterSummary = document.querySelector("#activeFilterSummary");
  const resetAuditFilters = document.querySelector("#resetAuditFilters");
  const customDateRange = document.querySelector("#customDateRange");
  const auditDetailDrawer = document.querySelector("#auditDetailDrawer");
  const exportAuditModal = document.querySelector("#exportAuditModal");
  const exportReason = document.querySelector("#exportReason");
  const exportReasonCount = document.querySelector("#exportReasonCount");
  const confirmExportButton = document.querySelector("#confirmExportButton");
  const toast = document.querySelector("#auditToast");
  const toastCopy = toast?.querySelector("p");

  const filters = {
    module: "all",
    action: "all",
    result: "all",
    date: "today",
    summary: "all"
  };

  let toastTimer = null;

  const rows = () => [...auditTableBody.querySelectorAll("tr")];
  const overlayElements = [auditDetailDrawer, exportAuditModal];

  const showToast = (message) => {
    if (!toast || !toastCopy) return;
    window.clearTimeout(toastTimer);
    toastCopy.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 3000);
  };

  const syncOverlayState = () => {
    body.classList.toggle(
      "audit-overlay-open",
      overlayElements.some((element) => element && !element.hidden)
    );
  };

  const openOverlay = (element) => {
    if (!element) return;
    element.hidden = false;
    syncOverlayState();
    window.setTimeout(() => {
      element.querySelector("button:not([disabled]), input:not([disabled]), textarea:not([disabled])")?.focus();
    }, 0);
  };

  const closeOverlay = (element) => {
    if (!element) return;
    element.hidden = true;
    syncOverlayState();
  };

  const closeSidebar = () => {
    sidebar.classList.remove("is-open");
    sidebarToggle.setAttribute("aria-expanded", "false");
    sidebarBackdrop.hidden = true;
  };

  const closeFilterMenus = (except = null) => {
    document.querySelectorAll(".audit-filter-menu").forEach((menu) => {
      if (menu === except) return;
      menu.hidden = true;
      menu.parentElement.querySelector(".audit-filter-trigger")?.setAttribute("aria-expanded", "false");
    });
  };

  const filterLabels = () => {
    const values = {};
    document.querySelectorAll(".audit-filter-select").forEach((container) => {
      values[container.dataset.filter] = container.querySelector(".audit-filter-trigger span").textContent;
    });
    const dateButton = document.querySelector(`[data-date-range="${filters.date}"]`);
    values.date = dateButton?.textContent || "今天";
    return values;
  };

  const updateFilterSummary = () => {
    const labels = filterLabels();
    activeFilterSummary.textContent = `${labels.date} · ${labels.module} · ${labels.action} · ${labels.result}`;
  };

  const matchesSummary = (row) => {
    if (filters.summary === "all") return true;
    if (filters.summary === "FAILED") return row.dataset.result === "FAILED";
    if (filters.summary === "SENSITIVE") return row.dataset.sensitive === "true";
    if (filters.summary === "ACTOR") return row.dataset.actor !== "";
    return true;
  };

  const applyFilters = () => {
    const keyword = auditSearch.value.trim().toLowerCase();
    let visible = 0;

    rows().forEach((row) => {
      const matchesKeyword = !keyword || row.textContent.toLowerCase().includes(keyword) ||
        row.dataset.trace.toLowerCase().includes(keyword) ||
        row.dataset.resource.toLowerCase().includes(keyword);
      const matchesModule = filters.module === "all" || row.dataset.module === filters.module;
      const matchesAction = filters.action === "all" || row.dataset.action === filters.action;
      const matchesResult = filters.result === "all" || row.dataset.result === filters.result;
      const visibleRow = matchesKeyword && matchesModule && matchesAction && matchesResult && matchesSummary(row);
      row.hidden = !visibleRow;
      if (visibleRow) visible += 1;
    });

    visibleAuditCount.textContent = String(visible);
    auditEmpty.hidden = visible > 0;
    document.querySelector(".audit-table-wrap").hidden = visible === 0;
    document.querySelector(".audit-list-footer").hidden = visible === 0;
    updateFilterSummary();
  };

  const resetFilters = () => {
    auditSearch.value = "";
    filters.module = "all";
    filters.action = "all";
    filters.result = "all";
    filters.date = "today";
    filters.summary = "all";

    document.querySelectorAll(".audit-filter-select").forEach((container) => {
      const firstOption = container.querySelector("[data-value='all']");
      container.querySelector(".audit-filter-trigger span").textContent = firstOption.textContent;
      container.querySelectorAll("[role='option']").forEach((option) => {
        const selected = option === firstOption;
        option.classList.toggle("is-selected", selected);
        option.setAttribute("aria-selected", String(selected));
      });
    });

    document.querySelectorAll("[data-date-range]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.dateRange === "today");
    });
    document.querySelectorAll("[data-summary-filter]").forEach((card) => {
      card.classList.toggle("is-filtering", card.dataset.summaryFilter === "all");
    });
    customDateRange.hidden = true;
    applyFilters();
  };

  const openAuditDetail = (row) => {
    const moduleText = row.querySelector(".module-badge").textContent;
    const actionText = row.querySelector("td:nth-child(2) > b").textContent;
    const resultSuccess = row.dataset.result === "SUCCESS";

    document.querySelector("#detailActionTitle").textContent = actionText;
    document.querySelector("#detailEventMeta").textContent = `${moduleText} · ${row.dataset.time}`;
    document.querySelector("#detailActor").textContent = row.dataset.actor;
    document.querySelector("#detailRole").textContent = row.dataset.role;
    document.querySelector("#detailIp").textContent = row.dataset.ip;
    document.querySelector("#detailClient").textContent = row.dataset.client;
    document.querySelector("#detailResource").textContent = row.dataset.resource;
    document.querySelector("#detailTrace").textContent = row.dataset.trace;
    document.querySelector("#detailBefore").textContent = row.dataset.before;
    document.querySelector("#detailAfter").textContent = row.dataset.after;
    document.querySelector("#detailContext").textContent = row.dataset.context;

    const resultIcon = document.querySelector("#detailResultIcon");
    const resultBadge = document.querySelector("#detailResultBadge");
    resultIcon.className = `event-result-icon ${resultSuccess ? "is-success" : "is-failed"}`;
    resultBadge.className = `result-badge ${resultSuccess ? "is-success" : "is-failed"}`;
    resultBadge.innerHTML = `<i></i>${resultSuccess ? "成功" : "失败"}`;

    openOverlay(auditDetailDrawer);
  };

  const copyDetailValue = async (targetId) => {
    const value = document.querySelector(`#${targetId}`).textContent;
    try {
      await navigator.clipboard.writeText(value);
      showToast("已复制到剪贴板。");
    } catch {
      showToast(`复制内容：${value}`);
    }
  };

  sidebarToggle.addEventListener("click", () => {
    const open = sidebar.classList.toggle("is-open");
    sidebarToggle.setAttribute("aria-expanded", String(open));
    sidebarBackdrop.hidden = !open;
  });

  sidebarBackdrop.addEventListener("click", closeSidebar);

  profileButton.addEventListener("click", (event) => {
    const open = profileMenu.hidden;
    profileMenu.hidden = !open;
    profileButton.setAttribute("aria-expanded", String(open));
    event.stopPropagation();
  });

  document.querySelector("[data-profile-action='security']")?.addEventListener("click", () => {
    profileMenu.hidden = true;
    profileButton.setAttribute("aria-expanded", "false");
    showToast("当前账号具备 admin.audit.view 权限，可查看全部项目审计日志。");
  });

  noticeButton.addEventListener("click", () => {
    showToast("后台提醒：1个API Key即将到期，1个Excel批次仍有待确认记录。");
  });

  document.querySelectorAll(".audit-filter-select").forEach((container) => {
    const trigger = container.querySelector(".audit-filter-trigger");
    const menu = container.querySelector(".audit-filter-menu");

    trigger.addEventListener("click", (event) => {
      const opening = menu.hidden;
      closeFilterMenus(opening ? menu : null);
      menu.hidden = !opening;
      trigger.setAttribute("aria-expanded", String(opening));
      event.stopPropagation();
    });

    menu.addEventListener("click", (event) => {
      const option = event.target.closest("[role='option']");
      if (!option) return;
      filters[container.dataset.filter] = option.dataset.value;
      trigger.querySelector("span").textContent = option.textContent;
      container.querySelectorAll("[role='option']").forEach((item) => {
        const selected = item === option;
        item.classList.toggle("is-selected", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      filters.summary = "all";
      document.querySelectorAll("[data-summary-filter]").forEach((card) => {
        card.classList.toggle("is-filtering", card.dataset.summaryFilter === "all");
      });
      applyFilters();
    });
  });

  auditSearch.addEventListener("input", applyFilters);
  auditSearch.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      auditSearch.value = "";
      applyFilters();
    }
  });

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      auditSearch.focus();
      return;
    }
    if (event.key !== "Escape") return;
    const openElement = [...overlayElements].reverse().find((element) => !element.hidden);
    if (openElement) {
      closeOverlay(openElement);
      return;
    }
    closeFilterMenus();
    if (!profileMenu.hidden) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
      return;
    }
    closeSidebar();
  });

  resetAuditFilters.addEventListener("click", resetFilters);
  document.querySelector("[data-reset-empty]").addEventListener("click", resetFilters);

  document.querySelectorAll("[data-summary-filter]").forEach((card) => {
    card.addEventListener("click", () => {
      filters.summary = card.dataset.summaryFilter;
      document.querySelectorAll("[data-summary-filter]").forEach((item) => {
        item.classList.toggle("is-filtering", item === card);
      });
      applyFilters();
    });
  });

  document.querySelectorAll("[data-date-range]").forEach((button) => {
    button.addEventListener("click", () => {
      filters.date = button.dataset.dateRange;
      document.querySelectorAll("[data-date-range]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      customDateRange.hidden = filters.date !== "custom";
      updateFilterSummary();
    });
  });

  document.querySelector("#applyDateRange").addEventListener("click", () => {
    const start = document.querySelector("#auditStartDate").value;
    const end = document.querySelector("#auditEndDate").value;
    if (!start || !end || start > end) {
      showToast("请选择正确的开始和结束日期。");
      return;
    }
    activeFilterSummary.textContent = `${start} 至 ${end} · ${filterLabels().module} · ${filterLabels().action} · ${filterLabels().result}`;
    showToast("已应用自定义时间范围。");
  });

  auditTableBody.addEventListener("click", (event) => {
    const button = event.target.closest("[data-view-audit]");
    if (!button) return;
    openAuditDetail(button.closest("tr"));
  });

  document.querySelectorAll("[data-close-audit-detail]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(auditDetailDrawer));
  });

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", () => copyDetailValue(button.dataset.copyTarget));
  });

  document.querySelector("#refreshAuditButton").addEventListener("click", (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.classList.add("is-loading");
    window.setTimeout(() => {
      button.disabled = false;
      button.classList.remove("is-loading");
      showToast("审计日志已刷新，当前为最新记录。");
    }, 700);
  });

  document.querySelector("#exportAuditButton").addEventListener("click", () => {
    const count = rows().filter((row) => !row.hidden).length;
    document.querySelector("#exportResultCount").textContent = `当前筛选结果：${count}条示例记录`;
    document.querySelector("#exportScopeText").textContent = activeFilterSummary.textContent;
    exportReason.value = "";
    exportReasonCount.textContent = "0";
    confirmExportButton.disabled = true;
    openOverlay(exportAuditModal);
  });

  exportReason.addEventListener("input", () => {
    const length = exportReason.value.length;
    exportReasonCount.textContent = String(length);
    confirmExportButton.disabled = exportReason.value.trim().length < 4;
  });

  document.querySelectorAll("[data-close-export]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(exportAuditModal));
  });

  confirmExportButton.addEventListener("click", () => {
    confirmExportButton.classList.add("is-loading");
    confirmExportButton.disabled = true;
    window.setTimeout(() => {
      confirmExportButton.classList.remove("is-loading");
      closeOverlay(exportAuditModal);
      showToast("审计日志导出任务已创建，导出操作已写入审计记录。");
    }, 850);
  });

  document.querySelectorAll(".audit-pagination button:not(:disabled)").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".audit-pagination button").forEach((item) => {
        item.classList.toggle("is-current", item === button && /^\d+$/.test(item.textContent.trim()));
        item.removeAttribute("aria-current");
      });
      if (/^\d+$/.test(button.textContent.trim())) button.setAttribute("aria-current", "page");
      showToast("已切换审计日志分页（原型演示）。");
    });
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".audit-filter-select")) closeFilterMenus();
    if (!profileMenu.hidden && !profileMenu.contains(event.target) && !profileButton.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 1040) closeSidebar();
  });

  applyFilters();
})();
