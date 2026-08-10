(() => {
  const body = document.body;
  const sidebar = document.querySelector("#adminSidebar");
  const sidebarToggle = document.querySelector("#sidebarToggle");
  const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
  const profileButton = document.querySelector("#adminProfileButton");
  const profileMenu = document.querySelector("#adminProfileMenu");
  const noticeButton = document.querySelector("#noticeButton");
  const providerList = document.querySelector("#providerList");
  const providerEmpty = document.querySelector("#providerEmpty");
  const providerSearch = document.querySelector("#providerSearch");
  const providerDrawer = document.querySelector("#providerDrawer");
  const providerForm = document.querySelector("#providerForm");
  const providerDrawerEyebrow = document.querySelector("#providerDrawerEyebrow");
  const providerDrawerTitle = document.querySelector("#providerDrawerTitle");
  const providerNameInput = document.querySelector("#providerNameInput");
  const providerUrlInput = document.querySelector("#providerUrlInput");
  const providerModelInput = document.querySelector("#providerModelInput");
  const providerKeyInput = document.querySelector("#providerKeyInput");
  const providerExpiryInput = document.querySelector("#providerExpiryInput");
  const providerTimeoutInput = document.querySelector("#providerTimeoutInput");
  const providerRetriesInput = document.querySelector("#providerRetriesInput");
  const keyFieldLabel = document.querySelector("#keyFieldLabel");
  const keyFieldHint = document.querySelector("#keyFieldHint");
  const toggleKeyVisibility = document.querySelector("#toggleKeyVisibility");
  const addProviderButton = document.querySelector("#addProviderButton");
  const testDraftButton = document.querySelector("#testDraftButton");
  const testAllButton = document.querySelector("#testAllButton");
  const testModal = document.querySelector("#testModal");
  const testProviderName = document.querySelector("#testProviderName");
  const testProviderMeta = document.querySelector("#testProviderMeta");
  const testOverallStatus = document.querySelector("#testOverallStatus");
  const testSteps = document.querySelector("#testSteps");
  const testResultNote = document.querySelector("#testResultNote");
  const runTestAgainButton = document.querySelector("#runTestAgainButton");
  const defaultModal = document.querySelector("#defaultModal");
  const defaultProviderName = document.querySelector("#defaultProviderName");
  const confirmDefaultButton = document.querySelector("#confirmDefaultButton");
  const securityModal = document.querySelector("#securityModal");
  const callDetailModal = document.querySelector("#callDetailModal");
  const rollbackDate = new Date("2026-07-24T00:00:00");
  const toast = document.querySelector("#apiToast");
  const toastCopy = toast?.querySelector("p");

  let activeProviderFilter = "all";
  let drawerMode = "create";
  let editingCard = null;
  let pendingDefaultCard = null;
  let activeTestTarget = null;
  let toastTimer = null;
  let testTimers = [];

  const providers = () => [...providerList.querySelectorAll(".provider-card")];

  const showToast = (message) => {
    if (!toast || !toastCopy) return;
    window.clearTimeout(toastTimer);
    toastCopy.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 3000);
  };

  const overlayElements = [providerDrawer, testModal, defaultModal, securityModal, callDetailModal];

  const syncOverlayState = () => {
    body.classList.toggle("api-overlay-open", overlayElements.some((element) => !element.hidden));
  };

  const openOverlay = (element) => {
    element.hidden = false;
    syncOverlayState();
    window.setTimeout(() => {
      element.querySelector("input:not([disabled]), button:not([disabled])")?.focus();
    }, 0);
  };

  const closeOverlay = (element) => {
    element.hidden = true;
    syncOverlayState();
  };

  const closeSidebar = () => {
    sidebar.classList.remove("is-open");
    sidebarToggle.setAttribute("aria-expanded", "false");
    sidebarBackdrop.hidden = true;
  };

  const closeProviderMenus = (except = null) => {
    document.querySelectorAll(".provider-more-menu").forEach((menu) => {
      if (menu === except) return;
      menu.hidden = true;
      menu.closest(".provider-actions")?.querySelector("[data-provider-more]")
        ?.setAttribute("aria-expanded", "false");
    });
  };

  const isExpiring = (dateText) => {
    if (!dateText) return false;
    const expiry = new Date(`${dateText}T00:00:00`);
    const days = Math.ceil((expiry - rollbackDate) / 86400000);
    return days >= 0 && days <= 30;
  };

  const getProviderName = (card) => card.dataset.providerName;

  const updateSummary = () => {
    const cards = providers();
    const healthy = cards.filter((card) => card.dataset.providerStatus === "ACTIVE").length;
    const expiring = cards.filter((card) => card.dataset.providerExpiring === "true").length;
    document.querySelector("#providerTotalCount").innerHTML = `${cards.length}<em>项</em>`;
    document.querySelector("#healthyProviderCount").innerHTML = `${healthy}<em>项</em>`;
    document.querySelector("#expiringProviderCount").innerHTML = `${expiring}<em>项</em>`;
    document.querySelector("#providerListCount").textContent = `${cards.length}项服务`;
    const expiryBadge = document.querySelector("#sidebarExpiryBadge");
    expiryBadge.textContent = String(expiring);
    expiryBadge.hidden = expiring === 0;
    expiryBadge.setAttribute("aria-label", `${expiring}项即将到期`);
  };

  const filterProviders = () => {
    const keyword = providerSearch.value.trim().toLowerCase();
    let visible = 0;
    providers().forEach((card) => {
      const searchText = [
        card.dataset.providerName,
        card.dataset.providerUrl,
        card.dataset.providerModel
      ].join(" ").toLowerCase();
      const statusMatch = activeProviderFilter === "all" || card.dataset.providerStatus === activeProviderFilter;
      const keywordMatch = !keyword || searchText.includes(keyword);
      card.hidden = !(statusMatch && keywordMatch);
      if (!card.hidden) visible += 1;
    });
    providerEmpty.hidden = visible > 0;
  };

  const resetProviderForm = () => {
    providerForm.reset();
    providerTimeoutInput.value = "60";
    providerRetriesInput.value = "2";
    providerKeyInput.type = "password";
    providerKeyInput.required = true;
  };

  const openProviderDrawer = (mode, card = null, focusKey = false) => {
    drawerMode = mode;
    editingCard = card;
    resetProviderForm();
    const editing = Boolean(card);
    providerDrawerEyebrow.textContent = editing ? "EDIT AI SERVICE" : "NEW AI SERVICE";
    providerDrawerTitle.textContent = editing ? "编辑 AI 服务" : "新增 AI 服务";
    keyFieldLabel.innerHTML = editing ? "更新 API Key" : "API Key <b>*</b>";
    keyFieldHint.textContent = editing
      ? "留空表示继续使用当前加密密钥；旧密钥不会回显。"
      : "仅在本次提交时使用明文，浏览器不会保存。";
    providerKeyInput.required = !editing;

    if (editing) {
      providerNameInput.value = card.dataset.providerName;
      providerUrlInput.value = card.dataset.providerUrl;
      providerModelInput.value = card.dataset.providerModel;
      providerExpiryInput.value = card.dataset.providerExpiry || "";
      providerTimeoutInput.value = card.dataset.providerTimeout;
      providerRetriesInput.value = card.dataset.providerRetries;
    }
    openOverlay(providerDrawer);
    window.setTimeout(() => (focusKey ? providerKeyInput : providerNameInput).focus(), 0);
  };

  const getMask = (key) => {
    const tail = key.slice(-4).toUpperCase().padStart(4, "•");
    return `sk-••••••••••••••••${tail}`;
  };

  const updateCardFromForm = (card, newCard = false) => {
    const name = providerNameInput.value.trim();
    const url = providerUrlInput.value.trim();
    const model = providerModelInput.value.trim();
    const expiry = providerExpiryInput.value;
    const timeout = providerTimeoutInput.value || "60";
    const retries = providerRetriesInput.value || "2";

    card.dataset.providerName = name;
    card.dataset.providerUrl = url;
    card.dataset.providerModel = model;
    card.dataset.providerExpiry = expiry;
    card.dataset.providerTimeout = timeout;
    card.dataset.providerRetries = retries;
    card.dataset.providerStatus = "ACTIVE";
    card.dataset.providerExpiring = String(isExpiring(expiry));
    card.classList.toggle("is-expiring", isExpiring(expiry));
    card.querySelector(".provider-name-row h3").textContent = name;
    card.querySelector(".provider-identity p").textContent = "OpenAI-compatible · 自定义服务通道";
    card.querySelector(".config-url strong").textContent = url;
    card.querySelectorAll(".config-field")[1].querySelector("strong").textContent = model;
    card.querySelectorAll(".config-field")[3].querySelector("strong").textContent = `${timeout}秒 / ${retries}次`;
    if (providerKeyInput.value) {
      card.querySelector(".config-key strong").textContent = getMask(providerKeyInput.value);
    }

    const expiryState = card.querySelector(".expiry-state");
    expiryState.classList.toggle("is-warning", isExpiring(expiry));
    expiryState.querySelector("strong").textContent = expiry ? `${expiry} 到期` : "未设置到期日";
    expiryState.querySelector("small").textContent = isExpiring(expiry) ? "30天内到期，建议尽快轮换" : "有效期正常";

    if (newCard) {
      card.dataset.providerId = `provider-${Date.now()}`;
      card.classList.remove("is-default");
      card.classList.toggle("is-expiring", isExpiring(expiry));
      card.querySelector(".provider-logo").className = "provider-logo provider-logo-green";
      const badge = card.querySelector(".priority-badge");
      badge.textContent = `备用 ${Math.max(2, providers().length)}`;
      card.querySelector(".provider-usage strong").textContent = "0次";
      card.querySelector(".provider-usage b").style.width = "0%";
      card.querySelector(".test-state strong").textContent = "尚未执行测试";
      card.querySelector(".test-state small").textContent = "建议保存后立即测试";
    }
  };

  const addProviderCard = () => {
    const template = providers().find((card) => !card.classList.contains("is-default")) || providers()[0];
    const card = template.cloneNode(true);
    updateCardFromForm(card, true);
    providerList.append(card);
    return card;
  };

  const validateDraft = () => {
    if (!providerNameInput.value.trim() || !providerUrlInput.value.trim() || !providerModelInput.value.trim()) {
      showToast("请先填写服务名称、接口地址和模型名称。");
      return false;
    }
    if (!providerUrlInput.value.trim().startsWith("https://")) {
      showToast("接口地址必须使用HTTPS。");
      providerUrlInput.focus();
      return false;
    }
    if (!editingCard && !providerKeyInput.value.trim()) {
      showToast("新增AI服务必须填写API Key。");
      providerKeyInput.focus();
      return false;
    }
    return true;
  };

  const clearTestTimers = () => {
    testTimers.forEach((timer) => window.clearTimeout(timer));
    testTimers = [];
  };

  const runConnectionTest = (target) => {
    clearTestTimers();
    activeTestTarget = target;
    const name = target.card ? getProviderName(target.card) : providerNameInput.value.trim();
    const model = target.card ? target.card.dataset.providerModel : providerModelInput.value.trim();
    const url = target.card ? target.card.dataset.providerUrl : providerUrlInput.value.trim();
    let host = url;
    try {
      host = new URL(url).host;
    } catch (error) {
      host = url;
    }
    testProviderName.textContent = name || "未命名服务";
    testProviderMeta.textContent = `${model || "未填写模型"} · ${host || "未填写地址"}`;
    testOverallStatus.className = "test-overall is-testing";
    testOverallStatus.innerHTML = "<i></i>测试中";
    testResultNote.hidden = true;
    [...testSteps.children].forEach((step, index) => {
      step.className = index === 0 ? "is-running" : "";
      step.querySelector("em").textContent = index === 0 ? "检测中" : "等待";
    });
    openOverlay(testModal);

    [...testSteps.children].forEach((step, index) => {
      testTimers.push(window.setTimeout(() => {
        step.className = "is-success";
        step.querySelector("em").textContent = "通过";
        const next = testSteps.children[index + 1];
        if (next) {
          next.className = "is-running";
          next.querySelector("em").textContent = "检测中";
        }
        if (index === testSteps.children.length - 1) {
          testOverallStatus.className = "test-overall is-success";
          testOverallStatus.innerHTML = "<i></i>测试通过";
          testResultNote.hidden = false;
          if (target.card) {
            const testState = target.card.querySelector(".test-state");
            testState.className = "test-state is-success";
            testState.querySelector("strong").textContent = "最近测试通过";
            testState.querySelector("small").textContent = "2026-07-24 刚刚 · 1.21秒";
          }
        }
      }, 420 * (index + 1)));
    });
  };

  const renderStrategy = () => {
    const defaultCard = providers().find((card) => card.classList.contains("is-default"));
    const backups = providers().filter((card) => card !== defaultCard && card.dataset.providerStatus === "ACTIVE");
    const items = document.querySelectorAll("#strategyList .strategy-item");
    const defaultItem = items[0];
    const backupItem = items[1];
    if (defaultCard) {
      defaultItem.dataset.strategyProvider = defaultCard.dataset.providerId;
      defaultItem.querySelector("strong").textContent = getProviderName(defaultCard);
      defaultItem.querySelector("small").textContent = "周报分析 · Agent问答";
    }
    if (backups[0]) {
      backupItem.hidden = false;
      document.querySelector(".strategy-connector").hidden = false;
      backupItem.dataset.strategyProvider = backups[0].dataset.providerId;
      backupItem.querySelector("strong").textContent = getProviderName(backups[0]);
      backupItem.querySelector("small").textContent = "仅默认服务不可用时调用";
    } else {
      backupItem.hidden = true;
      document.querySelector(".strategy-connector").hidden = true;
    }
  };

  const setDefaultProvider = (card) => {
    const current = providers().find((item) => item.classList.contains("is-default"));
    if (current === card) {
      showToast("该服务已经是默认服务。");
      return;
    }
    if (card.dataset.providerStatus !== "ACTIVE") {
      showToast("请先启用服务，再设为默认服务。");
      return;
    }
    pendingDefaultCard = card;
    defaultProviderName.textContent = getProviderName(card);
    openOverlay(defaultModal);
  };

  const applyDefaultProvider = () => {
    if (!pendingDefaultCard) return;
    providers().forEach((card, index) => {
      const selected = card === pendingDefaultCard;
      card.classList.toggle("is-default", selected);
      card.querySelector(".default-badge")?.remove();
      card.querySelector(".priority-badge")?.remove();
      const badge = document.createElement("span");
      badge.className = selected ? "default-badge" : "priority-badge";
      badge.textContent = selected ? "默认服务" : `备用 ${index + 1}`;
      card.querySelector(".provider-name-row h3").after(badge);
    });
    const name = getProviderName(pendingDefaultCard);
    renderStrategy();
    closeOverlay(defaultModal);
    pendingDefaultCard = null;
    showToast(`“${name}”已设为默认AI服务，并记录到审计日志。`);
  };

  const toggleProvider = (card) => {
    if (card.classList.contains("is-default") && card.dataset.providerStatus === "ACTIVE") {
      showToast("默认服务不能直接停用，请先切换默认服务。");
      return;
    }
    const enabling = card.dataset.providerStatus !== "ACTIVE";
    card.dataset.providerStatus = enabling ? "ACTIVE" : "DISABLED";
    const status = card.querySelector(".provider-status");
    status.className = `provider-status ${enabling ? "is-active" : "is-disabled"}`;
    status.innerHTML = `<i></i>${enabling ? "已启用" : "已停用"}`;
    card.querySelector("[data-toggle-provider]").textContent = enabling ? "启用服务" : "停用服务";
    renderStrategy();
    updateSummary();
    filterProviders();
    showToast(`“${getProviderName(card)}”已${enabling ? "启用" : "停用"}。`);
  };

  const renderUsage = (scene) => {
    const data = {
      all: ["2,846", "3.28M", "1.46s", "37", [56, 68, 49, 73, 88, 78, 92]],
      weekly: ["1,482", "2.04M", "1.72s", "21", [48, 59, 42, 66, 82, 71, 86]],
      agent: ["923", "0.81M", "1.08s", "9", [35, 43, 31, 47, 53, 51, 62]],
      risk: ["441", "0.43M", "1.31s", "7", [23, 29, 18, 34, 39, 32, 41]]
    }[scene];
    document.querySelector("#usageCalls").textContent = data[0];
    document.querySelector("#usageTokens").textContent = data[1];
    document.querySelector("#usageLatency").textContent = data[2];
    document.querySelector("#usageErrors").textContent = data[3];
    document.querySelectorAll(".chart-bars > div > span").forEach((bar, index) => {
      bar.style.height = `${data[4][index]}%`;
    });
  };

  const filterLogs = (filter) => {
    let visible = 0;
    document.querySelectorAll("#callLogBody tr").forEach((row) => {
      row.hidden = filter !== "all" && row.dataset.logStatus !== filter;
      if (!row.hidden) visible += 1;
    });
    document.querySelector("#callLogEmpty").hidden = visible > 0;
  };

  const openCallDetail = (row) => {
    const cells = row.cells;
    document.querySelector("#detailTraceId").textContent = cells[0].querySelector("code").textContent;
    document.querySelector("#detailCallTime").textContent = `2026-${cells[0].querySelector("strong").textContent}`;
    document.querySelector("#detailCallProvider").textContent = `${cells[1].querySelector("strong").textContent} / ${cells[1].querySelector("small").textContent}`;
    document.querySelector("#detailCallScene").textContent = cells[2].textContent.trim();
    document.querySelector("#detailCallUsage").textContent = `${cells[3].textContent.trim()} / ${cells[4].textContent.trim()}`;
    const result = document.querySelector("#detailCallResult");
    const success = row.dataset.logStatus === "SUCCESS";
    result.innerHTML = `<span class="call-result ${success ? "is-success" : "is-failed"}"><i></i>${success ? "成功" : "失败"}</span>`;
    document.querySelector("#detailCallError").textContent = success ? "无" : cells[6].textContent.trim();
    openOverlay(callDetailModal);
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
    showToast("账号安全：当前会话具备admin.ai.manage权限，最近无异常登录。");
  });

  noticeButton.addEventListener("click", () => {
    showToast("密钥提醒：生产分析服务将在2026年8月18日到期，剩余25天。");
  });

  document.querySelectorAll("[data-next-module]").forEach((button) => {
    button.addEventListener("click", () => showToast(`${button.dataset.nextModule}将在后续页面中逐页生成。`));
  });

  document.querySelectorAll("[data-provider-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      activeProviderFilter = button.dataset.providerFilter;
      document.querySelectorAll("[data-provider-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
      filterProviders();
    });
  });

  providerSearch.addEventListener("input", filterProviders);

  document.querySelectorAll("[data-summary-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      const filter = button.dataset.summaryFilter;
      if (filter === "EXPIRING") {
        activeProviderFilter = "all";
        providerSearch.value = "";
        filterProviders();
        const card = providers().find((item) => item.dataset.providerExpiring === "true");
        card?.scrollIntoView({ behavior: "smooth", block: "center" });
        card?.classList.add("is-highlighted");
        window.setTimeout(() => card?.classList.remove("is-highlighted"), 1800);
        return;
      }
      activeProviderFilter = filter;
      document.querySelectorAll("[data-provider-filter]").forEach((item) => item.classList.toggle("is-active", item.dataset.providerFilter === filter));
      filterProviders();
    });
  });

  document.querySelector("[data-clear-provider-filter]").addEventListener("click", () => {
    providerSearch.value = "";
    activeProviderFilter = "all";
    document.querySelectorAll("[data-provider-filter]").forEach((item) => item.classList.toggle("is-active", item.dataset.providerFilter === "all"));
    filterProviders();
  });

  providerList.addEventListener("click", (event) => {
    const card = event.target.closest(".provider-card");
    if (!card) return;
    const moreButton = event.target.closest("[data-provider-more]");
    if (moreButton) {
      const menu = card.querySelector(".provider-more-menu");
      const open = menu.hidden;
      closeProviderMenus(menu);
      menu.hidden = !open;
      moreButton.setAttribute("aria-expanded", String(open));
      event.stopPropagation();
      return;
    }
    if (event.target.closest("[data-test-provider]")) runConnectionTest({ card });
    if (event.target.closest("[data-edit-provider]")) openProviderDrawer("edit", card);
    if (event.target.closest("[data-rotate-key]")) openProviderDrawer("edit", card, true);
    if (event.target.closest("[data-set-default]")) setDefaultProvider(card);
    if (event.target.closest("[data-toggle-provider]")) toggleProvider(card);
    closeProviderMenus();
  });

  addProviderButton.addEventListener("click", () => openProviderDrawer("create"));
  document.querySelector("#rotatePrimaryButton").addEventListener("click", () => {
    openProviderDrawer("edit", providers().find((card) => card.classList.contains("is-default")), true);
  });

  document.querySelectorAll("[data-close-provider-drawer]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(providerDrawer));
  });

  toggleKeyVisibility.addEventListener("click", () => {
    const visible = providerKeyInput.type === "text";
    providerKeyInput.type = visible ? "password" : "text";
    toggleKeyVisibility.setAttribute("aria-label", visible ? "临时显示正在输入的密钥" : "隐藏正在输入的密钥");
  });

  testDraftButton.addEventListener("click", () => {
    if (validateDraft()) runConnectionTest({ card: null });
  });

  providerForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!validateDraft()) return;
    const saveButton = document.querySelector("#saveProviderButton");
    saveButton.classList.add("is-loading");
    saveButton.disabled = true;
    window.setTimeout(() => {
      const card = editingCard ? editingCard : addProviderCard();
      if (editingCard) updateCardFromForm(card);
      const name = providerNameInput.value.trim();
      saveButton.classList.remove("is-loading");
      saveButton.disabled = false;
      closeOverlay(providerDrawer);
      updateSummary();
      filterProviders();
      renderStrategy();
      showToast(`“${name}”的AI服务配置已${drawerMode === "create" ? "新增" : "更新"}，密钥已加密保存。`);
    }, 700);
  });

  testAllButton.addEventListener("click", () => {
    testAllButton.classList.add("is-loading");
    testAllButton.disabled = true;
    window.setTimeout(() => {
      testAllButton.classList.remove("is-loading");
      testAllButton.disabled = false;
      providers().forEach((card) => {
        const state = card.querySelector(".test-state");
        state.querySelector("strong").textContent = "最近测试通过";
        state.querySelector("small").textContent = "2026-07-24 刚刚 · 状态正常";
      });
      showToast(`全部${providers().length}项AI服务连接测试通过。`);
    }, 1050);
  });

  document.querySelectorAll("[data-close-test]").forEach((button) => button.addEventListener("click", () => closeOverlay(testModal)));
  runTestAgainButton.addEventListener("click", () => runConnectionTest(activeTestTarget));

  document.querySelectorAll("[data-close-default]").forEach((button) => button.addEventListener("click", () => closeOverlay(defaultModal)));
  confirmDefaultButton.addEventListener("click", applyDefaultProvider);

  document.querySelector("#securityDetailButton").addEventListener("click", () => openOverlay(securityModal));
  document.querySelector("#strategyHelpButton").addEventListener("click", () => {
    showToast("仅网络超时、429和5xx触发自动重试；密钥错误不会切换后继续重试。" );
  });
  document.querySelectorAll("[data-close-security]").forEach((button) => button.addEventListener("click", () => closeOverlay(securityModal)));

  document.querySelectorAll("[data-usage-scene]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-usage-scene]").forEach((item) => item.classList.toggle("is-active", item === button));
      renderUsage(button.dataset.usageScene);
    });
  });

  document.querySelectorAll("[data-log-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-log-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
      filterLogs(button.dataset.logFilter);
    });
  });

  document.querySelectorAll("[data-call-detail]").forEach((button) => {
    button.addEventListener("click", () => openCallDetail(button.closest("tr")));
  });
  document.querySelectorAll("[data-close-call-detail]").forEach((button) => button.addEventListener("click", () => closeOverlay(callDetailModal)));

  document.querySelectorAll(".table-pagination button:not([disabled])").forEach((button) => {
    button.addEventListener("click", () => showToast("原型页已保留分页交互，完整调用元数据将在接口联调后加载。"));
  });

  document.addEventListener("click", (event) => {
    if (!profileMenu.hidden && !profileMenu.contains(event.target) && !profileButton.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
    if (!event.target.closest(".provider-actions")) closeProviderMenus();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openOverlayElement = [callDetailModal, securityModal, defaultModal, testModal, providerDrawer]
      .find((element) => !element.hidden);
    if (openOverlayElement) {
      closeOverlay(openOverlayElement);
      return;
    }
    if (!profileMenu.hidden) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
      profileButton.focus();
      return;
    }
    closeProviderMenus();
    closeSidebar();
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 1040) closeSidebar();
  });

  updateSummary();
  renderStrategy();
})();
