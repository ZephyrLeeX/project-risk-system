(() => {
  const body = document.body;
  const sidebar = document.querySelector("#adminSidebar");
  const sidebarToggle = document.querySelector("#sidebarToggle");
  const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
  const profileButton = document.querySelector("#adminProfileButton");
  const profileMenu = document.querySelector("#adminProfileMenu");
  const noticeButton = document.querySelector("#noticeButton");
  const batchMoreButton = document.querySelector("#batchMoreButton");
  const batchMoreMenu = document.querySelector("#batchMoreMenu");
  const toast = document.querySelector("#importsToast");
  const toastCopy = toast?.querySelector("p");
  const tabButtons = [...document.querySelectorAll("[data-import-tab]")];
  const tabPanels = {
    preview: document.querySelector("#previewPanel"),
    pending: document.querySelector("#pendingPanel"),
    rules: document.querySelector("#rulesPanel")
  };
  const previewRows = [...document.querySelectorAll("#previewTableBody tr[data-sheet]")];
  const previewEmpty = document.querySelector("#previewEmpty");
  const currentSheetLabel = document.querySelector("#currentSheetLabel");
  const showAllSheetsButton = document.querySelector("#showAllSheetsButton");
  const warningConfirm = document.querySelector("#warningConfirm");
  const publishButton = document.querySelector("#publishBatchButton");
  const publishModal = document.querySelector("#publishModal");
  const confirmPublishButton = document.querySelector("#confirmPublishButton");
  const saveDraftButton = document.querySelector("#saveDraftButton");
  const activeBatchStatus = document.querySelector("#activeBatchStatus");
  const summaryPendingCount = document.querySelector("#summaryPendingCount");
  const validationPendingCount = document.querySelector("#validationPendingCount");
  const pendingTabCount = document.querySelector("#pendingTabCount");
  const stepPendingCopy = document.querySelector("#stepPendingCopy");
  const sidebarPendingBadge = document.querySelector("#sidebarPendingBadge");
  const importSteps = document.querySelector("#importSteps");
  const uploadDrawer = document.querySelector("#uploadDrawer");
  const newImportButton = document.querySelector("#newImportButton");
  const fileInput = document.querySelector("#excelFileInput");
  const fileDropZone = document.querySelector("#fileDropZone");
  const selectedUploadFile = document.querySelector("#selectedUploadFile");
  const selectedFileName = document.querySelector("#selectedFileName");
  const selectedFileMeta = document.querySelector("#selectedFileMeta");
  const removeSelectedFile = document.querySelector("#removeSelectedFile");
  const parseFileButton = document.querySelector("#parseFileButton");
  const activeImportTitle = document.querySelector("#activeImportTitle");
  const batchDetailModal = document.querySelector("#batchDetailModal");
  const detailFileName = document.querySelector("#detailFileName");
  const detailBatchCode = document.querySelector("#detailBatchCode");
  const detailBatchStatus = document.querySelector("#detailBatchStatus");
  const rollbackModal = document.querySelector("#rollbackModal");
  const rollbackBatchCode = document.querySelector("#rollbackBatchCode");
  const rollbackConfirmInput = document.querySelector("#rollbackConfirmInput");
  const confirmRollbackButton = document.querySelector("#confirmRollbackButton");
  const historyRows = [...document.querySelectorAll("#historyTableBody tr[data-history-status]")];
  const historyEmpty = document.querySelector("#historyEmpty");

  let selectedFile = null;
  let toastTimer = null;
  let rollbackTargetRow = null;
  let published = false;

  const showToast = (message) => {
    if (!toast || !toastCopy) return;
    window.clearTimeout(toastTimer);
    toastCopy.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 3000);
  };

  const overlayIsOpen = () =>
    [uploadDrawer, publishModal, batchDetailModal, rollbackModal].some(
      (element) => element && !element.hidden
    );

  const syncOverlayState = () => {
    body.classList.toggle("has-import-overlay", overlayIsOpen());
  };

  const openOverlay = (element) => {
    if (!element) return;
    element.hidden = false;
    syncOverlayState();
    window.setTimeout(() => {
      element.querySelector("button:not([disabled]), input:not([disabled])")?.focus();
    }, 0);
  };

  const closeOverlay = (element) => {
    if (!element) return;
    element.hidden = true;
    syncOverlayState();
  };

  const closeSidebar = () => {
    sidebar?.classList.remove("is-open");
    sidebarToggle?.setAttribute("aria-expanded", "false");
    if (sidebarBackdrop) sidebarBackdrop.hidden = true;
  };

  const closeMenus = () => {
    if (batchMoreMenu) batchMoreMenu.hidden = true;
    batchMoreButton?.setAttribute("aria-expanded", "false");
  };

  const openTab = (tabName) => {
    if (!tabPanels[tabName]) return;
    tabButtons.forEach((button) => {
      const active = button.dataset.importTab === tabName;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    Object.entries(tabPanels).forEach(([key, panel]) => {
      panel.hidden = key !== tabName;
    });
    if (tabName === "pending") {
      document.querySelector("#pendingTab")?.scrollIntoView({
        behavior: "smooth",
        block: "nearest"
      });
    }
  };

  const filterPreviewRows = (sheetName = "") => {
    let visibleCount = 0;
    previewRows.forEach((row) => {
      const visible = !sheetName || row.dataset.sheet === sheetName;
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    if (currentSheetLabel) currentSheetLabel.textContent = sheetName || "全部工作表";
    if (previewEmpty) previewEmpty.hidden = visibleCount > 0;
    document.querySelectorAll("[data-sheet-filter]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.sheetFilter === sheetName);
    });
    openTab("preview");
  };

  const getResolvedCount = () =>
    document.querySelectorAll(".match-decision-card input[type='radio']:checked").length;

  const updatePendingState = () => {
    const total = document.querySelectorAll(".match-decision-card").length;
    const pending = Math.max(0, total - getResolvedCount());

    if (summaryPendingCount) {
      summaryPendingCount.innerHTML = `${pending}<em>条</em>`;
    }
    if (validationPendingCount) validationPendingCount.textContent = String(pending);
    if (pendingTabCount) pendingTabCount.textContent = String(pending);
    if (stepPendingCopy) {
      stepPendingCopy.textContent = pending ? `${pending}条匹配待确认` : "差异已全部确认";
    }
    if (sidebarPendingBadge) {
      sidebarPendingBadge.textContent = String(pending);
      sidebarPendingBadge.hidden = pending === 0;
      sidebarPendingBadge.setAttribute("aria-label", `${pending}项待确认`);
    }

    publishButton.disabled = published || pending > 0 || !warningConfirm.checked;
    publishButton.title = pending
      ? `仍有${pending}条项目匹配待确认`
      : warningConfirm.checked
        ? ""
        : "请先确认聚合警告";
  };

  const formatFileSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  const validateFile = (file) => {
    const name = file.name.toLowerCase();
    if (name.endsWith(".xlsm")) {
      return "不支持包含宏的.xlsm文件，请另存为.xlsx后重试。";
    }
    if (!name.endsWith(".xlsx") && !name.endsWith(".xls")) {
      return "请选择.xlsx或.xls格式的Excel文件。";
    }
    if (file.size > 20 * 1024 * 1024) {
      return "文件超过20MB限制，请拆分或压缩数据后重试。";
    }
    return "";
  };

  const setSelectedFile = (file) => {
    const error = validateFile(file);
    if (error) {
      showToast(error);
      if (fileInput) fileInput.value = "";
      return;
    }
    selectedFile = file;
    selectedFileName.textContent = file.name;
    selectedFileMeta.textContent = `${formatFileSize(file.size)} · 待解析`;
    selectedUploadFile.hidden = false;
    parseFileButton.disabled = false;
  };

  const clearSelectedFile = () => {
    selectedFile = null;
    if (fileInput) fileInput.value = "";
    if (selectedUploadFile) selectedUploadFile.hidden = true;
    if (parseFileButton) parseFileButton.disabled = true;
  };

  const statusText = (status) => {
    if (status === "SUCCEEDED") return "已发布";
    if (status === "ROLLED_BACK") return "已回滚";
    return "待确认";
  };

  const statusClass = (status) => {
    if (status === "SUCCEEDED") return "is-success";
    if (status === "ROLLED_BACK") return "is-rollback";
    return "is-preview";
  };

  const openBatchDetail = (row) => {
    if (!row) return;
    const status = row.dataset.historyStatus;
    detailFileName.textContent = row.dataset.batchFile;
    detailBatchCode.textContent = row.dataset.batchId;
    detailBatchStatus.className = `history-status ${statusClass(status)}`;
    detailBatchStatus.innerHTML = `<i></i>${statusText(status)}`;
    openOverlay(batchDetailModal);
  };

  const openRollback = (row) => {
    rollbackTargetRow = row;
    rollbackBatchCode.textContent = row.dataset.batchId;
    rollbackConfirmInput.value = "";
    confirmRollbackButton.disabled = true;
    openOverlay(rollbackModal);
    rollbackConfirmInput.focus();
  };

  const renderHistoryFilter = (filter) => {
    let visibleCount = 0;
    historyRows.forEach((row) => {
      const visible = filter === "all" || row.dataset.historyStatus === filter;
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    historyEmpty.hidden = visibleCount > 0;
    document.querySelectorAll("[data-history-filter]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.historyFilter === filter);
    });
  };

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => openTab(button.dataset.importTab));
  });

  document.querySelectorAll("[data-open-tab]").forEach((button) => {
    button.addEventListener("click", () => openTab(button.dataset.openTab));
  });

  document.querySelectorAll("[data-sheet-filter]").forEach((button) => {
    button.addEventListener("click", () => filterPreviewRows(button.dataset.sheetFilter));
  });

  showAllSheetsButton?.addEventListener("click", () => filterPreviewRows());

  document.querySelectorAll(".match-decision-card input[type='radio']").forEach((input) => {
    input.addEventListener("change", () => {
      const card = input.closest(".match-decision-card");
      const state = card?.querySelector(".decision-state");
      if (state) {
        state.textContent = input.value === "CREATE" ? "已确认新建" : "已确认关联";
        state.classList.add("is-resolved");
      }
      updatePendingState();
      showToast("匹配选择已暂存，发布批次后正式生效。");
    });
  });

  warningConfirm?.addEventListener("change", updatePendingState);

  sidebarToggle?.addEventListener("click", () => {
    const open = sidebar.classList.toggle("is-open");
    sidebarToggle.setAttribute("aria-expanded", String(open));
    sidebarBackdrop.hidden = !open;
  });

  sidebarBackdrop?.addEventListener("click", closeSidebar);

  profileButton?.addEventListener("click", (event) => {
    const open = profileMenu.hidden;
    profileMenu.hidden = !open;
    profileButton.setAttribute("aria-expanded", String(open));
    event.stopPropagation();
  });

  document.querySelector("[data-profile-action='security']")?.addEventListener("click", () => {
    profileMenu.hidden = true;
    profileButton.setAttribute("aria-expanded", "false");
    showToast("账号安全：当前会话权限正常，最近无异常登录。");
  });

  noticeButton?.addEventListener("click", () => {
    showToast("导入提醒：当前批次有2条项目匹配待确认。");
  });

  document.querySelectorAll("[data-next-module]").forEach((button) => {
    button.addEventListener("click", () => {
      showToast(`${button.dataset.nextModule}将在后续页面中逐页生成。`);
      if (window.innerWidth <= 1040) closeSidebar();
    });
  });

  batchMoreButton?.addEventListener("click", (event) => {
    const open = batchMoreMenu.hidden;
    batchMoreMenu.hidden = !open;
    batchMoreButton.setAttribute("aria-expanded", String(open));
    event.stopPropagation();
  });

  batchMoreMenu?.addEventListener("click", (event) => {
    const action = event.target.closest("[data-batch-menu-action]")?.dataset.batchMenuAction;
    if (!action) return;
    closeMenus();
    if (action === "download-source") showToast("原始文件下载任务已创建。");
    if (action === "save-draft") showToast("当前导入草稿已保存。");
    if (action === "discard") showToast("演示页面未执行放弃操作，当前批次仍保留。");
  });

  saveDraftButton?.addEventListener("click", () => {
    saveDraftButton.classList.add("is-loading");
    window.setTimeout(() => {
      saveDraftButton.classList.remove("is-loading");
      showToast("导入草稿已保存，匹配选择和警告确认状态已记录。");
    }, 650);
  });

  publishButton?.addEventListener("click", () => {
    if (publishButton.disabled) return;
    openOverlay(publishModal);
  });

  document.querySelectorAll("[data-close-publish]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(publishModal));
  });

  confirmPublishButton?.addEventListener("click", () => {
    confirmPublishButton.classList.add("is-loading");
    confirmPublishButton.disabled = true;
    window.setTimeout(() => {
      published = true;
      closeOverlay(publishModal);
      confirmPublishButton.classList.remove("is-loading");
      confirmPublishButton.disabled = false;
      activeBatchStatus.className = "batch-status is-success";
      activeBatchStatus.innerHTML = "<i></i>已发布";
      publishButton.disabled = true;
      publishButton.innerHTML = "<span aria-hidden='true'></span>批次已发布";
      warningConfirm.disabled = true;
      document.querySelector(".summary-version strong").innerHTML = "V19";
      document.querySelector(".summary-version i").textContent = "07-24 刚刚发布";

      const steps = [...importSteps.children];
      steps.forEach((step) => {
        step.classList.remove("is-current");
        step.classList.add("is-complete");
      });
      if (steps[3]) steps[3].classList.add("is-current");
      if (steps[3]?.querySelector("small")) {
        steps[3].querySelector("small").textContent = "V19已生效";
      }

      const currentRow = historyRows.find(
        (row) => row.dataset.batchId === document.querySelector("#activeBatchCode").textContent
      );
      if (currentRow) {
        currentRow.dataset.historyStatus = "SUCCEEDED";
        const status = currentRow.querySelector(".history-status");
        status.className = "history-status is-success";
        status.innerHTML = "<i></i>已发布";
        const pending = currentRow.querySelector(".history-pending");
        if (pending) {
          pending.className = "history-ok";
          pending.textContent = "0待确认";
        }
      }
      showToast("批次发布成功，数据版本已由V18更新为V19。");
    }, 950);
  });

  newImportButton?.addEventListener("click", () => {
    clearSelectedFile();
    openOverlay(uploadDrawer);
  });

  document.querySelectorAll("[data-close-upload]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(uploadDrawer));
  });

  fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (file) setSelectedFile(file);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    fileDropZone?.addEventListener(eventName, (event) => {
      event.preventDefault();
      fileDropZone.classList.add("is-dragover");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    fileDropZone?.addEventListener(eventName, (event) => {
      event.preventDefault();
      fileDropZone.classList.remove("is-dragover");
    });
  });

  fileDropZone?.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) setSelectedFile(file);
  });

  fileDropZone?.querySelector("button")?.addEventListener("click", (event) => {
    event.preventDefault();
    fileInput.click();
  });

  removeSelectedFile?.addEventListener("click", clearSelectedFile);

  parseFileButton?.addEventListener("click", () => {
    if (!selectedFile) return;
    parseFileButton.classList.add("is-loading");
    parseFileButton.disabled = true;
    selectedFileMeta.textContent = `${formatFileSize(selectedFile.size)} · 正在解析`;
    window.setTimeout(() => {
      parseFileButton.classList.remove("is-loading");
      parseFileButton.disabled = false;
      if (activeImportTitle) activeImportTitle.textContent = selectedFile.name;
      closeOverlay(uploadDrawer);
      showToast(`“${selectedFile.name}”上传完成，已识别4张工作表并生成校验预览。`);
    }, 1050);
  });

  document.querySelector("#downloadTemplateButton")?.addEventListener("click", () => {
    showToast("导入说明已准备：包含4张工作表结构、字段含义和校验规则。");
  });

  document.querySelector("#downloadErrorButton")?.addEventListener("click", () => {
    showToast("校验明细已生成，包含130条编码缺失和2条待确认记录。");
  });

  document.querySelectorAll("[data-history-filter]").forEach((button) => {
    button.addEventListener("click", () => renderHistoryFilter(button.dataset.historyFilter));
  });

  document.querySelectorAll("[data-batch-detail]").forEach((button) => {
    button.addEventListener("click", () => openBatchDetail(button.closest("tr")));
  });

  document.querySelectorAll("[data-close-batch-detail]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(batchDetailModal));
  });

  document.querySelectorAll("[data-rollback-batch]").forEach((button) => {
    button.addEventListener("click", () => openRollback(button.closest("tr")));
  });

  document.querySelectorAll("[data-close-rollback]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(rollbackModal));
  });

  rollbackConfirmInput?.addEventListener("input", () => {
    confirmRollbackButton.disabled = rollbackConfirmInput.value.trim() !== "回滚";
  });

  confirmRollbackButton?.addEventListener("click", () => {
    if (!rollbackTargetRow || confirmRollbackButton.disabled) return;
    confirmRollbackButton.classList.add("is-loading");
    confirmRollbackButton.disabled = true;
    window.setTimeout(() => {
      rollbackTargetRow.dataset.historyStatus = "ROLLED_BACK";
      const status = rollbackTargetRow.querySelector(".history-status");
      status.className = "history-status is-rollback";
      status.innerHTML = "<i></i>已回滚";
      rollbackTargetRow.querySelector("[data-rollback-batch]")?.remove();
      const batchId = rollbackTargetRow.dataset.batchId;
      closeOverlay(rollbackModal);
      confirmRollbackButton.classList.remove("is-loading");
      rollbackTargetRow = null;
      renderHistoryFilter(
        document.querySelector("[data-history-filter].is-active")?.dataset.historyFilter || "all"
      );
      showToast(`批次${batchId}已回滚，并生成新的审计记录。`);
    }, 900);
  });

  document.querySelectorAll(".preview-pagination button:not([disabled]), .history-pagination button:not([disabled])")
    .forEach((button) => {
      button.addEventListener("click", () => {
        showToast("原型页已保留分页交互，完整数据将在接口联调后加载。");
      });
    });

  document.addEventListener("click", (event) => {
    if (!profileMenu.hidden && !profileMenu.contains(event.target) && !profileButton.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
    if (!batchMoreMenu.hidden && !batchMoreMenu.contains(event.target) && !batchMoreButton.contains(event.target)) {
      closeMenus();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!rollbackModal.hidden) {
      closeOverlay(rollbackModal);
      return;
    }
    if (!batchDetailModal.hidden) {
      closeOverlay(batchDetailModal);
      return;
    }
    if (!publishModal.hidden) {
      closeOverlay(publishModal);
      return;
    }
    if (!uploadDrawer.hidden) {
      closeOverlay(uploadDrawer);
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

  updatePendingState();
})();
