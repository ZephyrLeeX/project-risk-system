(function () {
  "use strict";

  const form = document.getElementById("mailboxForm");
  const formState = document.getElementById("formState");
  const emailAddress = document.getElementById("emailAddress");
  const imapHost = document.getElementById("imapHost");
  const imapPort = document.getElementById("imapPort");
  const authCode = document.getElementById("authCode");
  const keywordInput = document.getElementById("keywordInput");
  const keywordList = document.getElementById("keywordList");
  const connectionResult = document.getElementById("connectionResult");
  const syncProgress = document.getElementById("syncProgress");
  const syncResult = document.getElementById("syncResult");
  const manualSyncButton = document.getElementById("manualSyncButton");
  const toggleMailboxButton = document.getElementById("toggleMailboxButton");
  const mailboxActiveBadge = document.getElementById("mailboxActiveBadge");
  const disableDialog = document.getElementById("disableDialog");
  const guideDialog = document.getElementById("guideDialog");
  const toast = document.getElementById("mailToast");
  let toastTimer = 0;
  let mailboxEnabled = true;
  let dirty = false;

  const initialState = {
    provider: "qq",
    email: "liufeng@example.com",
    host: "imap.qq.com",
    port: "993",
    encryption: "ssl",
    folder: "INBOX",
    range: "4",
    sender: "",
    attachments: true,
    ai: true,
    keywords: ["项目周报", "工作周报", "风险周报"]
  };

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.querySelector("p").textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(function () {
      toast.hidden = true;
    }, 2400);
  }

  function setDirty(nextDirty) {
    dirty = nextDirty;
    formState.classList.toggle("is-dirty", nextDirty);
    formState.innerHTML = nextDirty ? "<i></i>存在未保存修改" : "<i></i>配置已保存";
  }

  function closeSelects(except) {
    document.querySelectorAll(".mail-select").forEach(function (select) {
      if (select === except) return;
      const trigger = select.querySelector(".mail-select-trigger");
      const menu = select.querySelector(".mail-select-menu");
      trigger.setAttribute("aria-expanded", "false");
      menu.hidden = true;
    });
  }

  function setSelectValue(select, value) {
    const option = select.querySelector(`[data-value="${value}"]`);
    if (!option) return;
    select.querySelectorAll("[role='option']").forEach(function (item) {
      item.setAttribute("aria-selected", String(item === option));
    });
    select.querySelector(".mail-select-trigger > span").textContent = option.textContent.trim();
    select.querySelector("input[type='hidden']").value = value;
  }

  document.querySelectorAll(".mail-select").forEach(function (select) {
    const trigger = select.querySelector(".mail-select-trigger");
    const menu = select.querySelector(".mail-select-menu");

    trigger.addEventListener("click", function () {
      const expanded = trigger.getAttribute("aria-expanded") === "true";
      closeSelects(select);
      trigger.setAttribute("aria-expanded", String(!expanded));
      menu.hidden = expanded;
    });

    menu.addEventListener("click", function (event) {
      const option = event.target.closest("[role='option']");
      if (!option) return;
      setSelectValue(select, option.dataset.value);
      trigger.setAttribute("aria-expanded", "false");
      menu.hidden = true;
      setDirty(true);
    });
  });

  document.querySelectorAll("input[name='provider']").forEach(function (radio) {
    radio.addEventListener("change", function () {
      document.querySelectorAll(".provider-card").forEach(function (card) {
        card.classList.toggle("is-selected", card.contains(radio));
      });
      const isQq = radio.value === "qq";
      document.getElementById("qqGuide").hidden = !isQq;
      if (isQq) {
        imapHost.value = "imap.qq.com";
        imapPort.value = "993";
        setSelectValue(document.querySelector("[data-select='encryption']"), "ssl");
      } else {
        imapHost.value = "";
        imapPort.value = "993";
        imapHost.focus();
      }
      connectionResult.hidden = true;
      setDirty(true);
    });
  });

  function clearError(input) {
    const error = document.querySelector(`[data-error-for="${input.id}"]`);
    input.classList.remove("has-error");
    if (error) error.textContent = "";
  }

  function setError(input, message) {
    const error = document.querySelector(`[data-error-for="${input.id}"]`);
    input.classList.add("has-error");
    if (error) error.textContent = message;
  }

  function validateConnectionFields() {
    let valid = true;
    [emailAddress, imapHost, imapPort].forEach(clearError);

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailAddress.value.trim())) {
      setError(emailAddress, "请输入有效的邮箱地址");
      valid = false;
    }
    if (!imapHost.value.trim() || !imapHost.value.includes(".")) {
      setError(imapHost, "请输入有效的IMAP服务器地址");
      valid = false;
    }
    const port = Number(imapPort.value);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setError(imapPort, "端口范围应为1至65535");
      valid = false;
    }

    if (!valid) {
      form.querySelector(".has-error")?.focus();
    }
    return valid;
  }

  document.getElementById("toggleAuthCode").addEventListener("click", function (event) {
    const button = event.currentTarget;
    const shouldShow = authCode.type === "password";
    authCode.type = shouldShow ? "text" : "password";
    button.setAttribute("aria-pressed", String(shouldShow));
    button.setAttribute("aria-label", shouldShow ? "隐藏授权码" : "显示授权码");
  });

  function addKeyword() {
    const value = keywordInput.value.trim();
    if (!value) {
      keywordInput.focus();
      return;
    }
    const existing = Array.from(keywordList.querySelectorAll(".keyword-chip")).map(function (chip) {
      return chip.firstChild.textContent.trim();
    });
    if (existing.includes(value)) {
      showToast("该关键词已存在");
      keywordInput.select();
      return;
    }
    if (existing.length >= 8) {
      showToast("最多可配置8个主题关键词");
      return;
    }
    const chip = document.createElement("span");
    chip.className = "keyword-chip";
    chip.append(document.createTextNode(value));
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.setAttribute("aria-label", `删除关键词 ${value}`);
    removeButton.textContent = "×";
    chip.append(removeButton);
    keywordList.append(chip);
    keywordInput.value = "";
    setDirty(true);
  }

  document.getElementById("addKeywordButton").addEventListener("click", addKeyword);
  keywordInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      addKeyword();
    }
  });

  keywordList.addEventListener("click", function (event) {
    const removeButton = event.target.closest("button");
    if (!removeButton) return;
    if (keywordList.querySelectorAll(".keyword-chip").length === 1) {
      showToast("至少保留一个主题关键词");
      return;
    }
    removeButton.closest(".keyword-chip").remove();
    setDirty(true);
  });

  form.addEventListener("input", function (event) {
    if (event.target.matches("input")) {
      clearError(event.target);
      setDirty(true);
      connectionResult.hidden = true;
    }
  });

  form.addEventListener("change", function () {
    setDirty(true);
  });

  document.getElementById("testConnectionButton").addEventListener("click", function (event) {
    if (!validateConnectionFields()) return;
    const button = event.currentTarget;
    button.classList.add("is-loading");
    button.disabled = true;
    button.lastChild.textContent = " 正在测试";
    connectionResult.hidden = true;

    window.setTimeout(function () {
      button.classList.remove("is-loading");
      button.disabled = false;
      button.lastChild.textContent = " 测试连接";
      connectionResult.hidden = false;
      document.getElementById("connectionResultTime").textContent = "刚刚";
      document.getElementById("connectionTestAt").textContent = "最近测试：刚刚";
      document.getElementById("connectionStatusText").innerHTML = "<i></i>连接正常";
      showToast("邮箱连接测试通过");
    }, 950);
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!validateConnectionFields()) return;
    if (!keywordList.querySelector(".keyword-chip")) {
      showToast("请至少配置一个主题关键词");
      keywordInput.focus();
      return;
    }

    const button = document.getElementById("saveMailboxButton");
    button.classList.add("is-loading");
    button.disabled = true;
    button.lastChild.textContent = " 正在保存";

    window.setTimeout(function () {
      button.classList.remove("is-loading");
      button.disabled = false;
      button.lastChild.textContent = " 保存配置";
      authCode.value = "";
      authCode.type = "password";
      document.getElementById("toggleAuthCode").setAttribute("aria-pressed", "false");
      setDirty(false);
      showToast("个人邮箱配置已安全保存");
    }, 720);
  });

  function restoreInitialState() {
    const radio = document.querySelector(`input[name="provider"][value="${initialState.provider}"]`);
    radio.checked = true;
    radio.dispatchEvent(new Event("change"));
    emailAddress.value = initialState.email;
    imapHost.value = initialState.host;
    imapPort.value = initialState.port;
    authCode.value = "";
    document.getElementById("senderRule").value = initialState.sender;
    document.getElementById("readAttachments").checked = initialState.attachments;
    document.getElementById("enableAiAnalysis").checked = initialState.ai;
    setSelectValue(document.querySelector("[data-select='encryption']"), initialState.encryption);
    setSelectValue(document.querySelector("[data-select='folder']"), initialState.folder);
    setSelectValue(document.querySelector("[data-select='range']"), initialState.range);
    keywordList.innerHTML = "";
    initialState.keywords.forEach(function (keyword) {
      const chip = document.createElement("span");
      chip.className = "keyword-chip";
      chip.append(document.createTextNode(keyword));
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-label", `删除关键词 ${keyword}`);
      button.textContent = "×";
      chip.append(button);
      keywordList.append(chip);
    });
    [emailAddress, imapHost, imapPort].forEach(clearError);
    connectionResult.hidden = true;
    setDirty(false);
    showToast("已撤销本次修改");
  }

  document.getElementById("discardButton").addEventListener("click", function () {
    if (!dirty) {
      showToast("当前没有未保存的修改");
      return;
    }
    restoreInitialState();
  });

  function openDialog(dialog) {
    dialog.hidden = false;
    document.body.style.overflow = "hidden";
    dialog.querySelector("button")?.focus();
  }

  function closeDialog(dialog) {
    dialog.hidden = true;
    document.body.style.overflow = "";
  }

  document.getElementById("showGuideButton").addEventListener("click", function () {
    openDialog(guideDialog);
  });

  document.querySelectorAll("[data-close-dialog]").forEach(function (button) {
    button.addEventListener("click", function () {
      closeDialog(button.closest(".mail-dialog"));
    });
  });

  document.querySelectorAll(".mail-dialog").forEach(function (dialog) {
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closeDialog(dialog);
    });
  });

  manualSyncButton.addEventListener("click", function () {
    if (!mailboxEnabled) {
      showToast("请先恢复邮箱后再执行同步");
      return;
    }
    if (dirty) {
      showToast("请先保存当前配置后再同步");
      return;
    }

    manualSyncButton.classList.add("is-loading");
    manualSyncButton.disabled = true;
    manualSyncButton.lastChild.textContent = " 正在同步";
    syncResult.hidden = true;
    syncProgress.hidden = false;
    const progressText = document.getElementById("syncProgressText");
    const percent = document.getElementById("syncPercent");
    const stages = [
      ["正在检查新增邮件…", "30%"],
      ["正在解析正文与附件…", "58%"],
      ["正在提取风险线索…", "82%"],
      ["正在写入同步结果…", "96%"]
    ];
    let stage = 0;

    const interval = window.setInterval(function () {
      stage += 1;
      if (stage < stages.length) {
        progressText.textContent = stages[stage][0];
        percent.textContent = stages[stage][1];
      }
    }, 430);

    window.setTimeout(function () {
      window.clearInterval(interval);
      syncProgress.hidden = true;
      syncResult.hidden = false;
      manualSyncButton.classList.remove("is-loading");
      manualSyncButton.disabled = false;
      manualSyncButton.lastChild.textContent = " 立即同步最新周报";
      document.getElementById("lastSyncAt").textContent = "刚刚";
      document.getElementById("lastSyncSummary").textContent = "新增5封 · 提取12项风险线索";
      document.getElementById("syncFinishedAt").textContent = "刚刚";
      showToast("同步完成：新增5封邮件，提取12项风险线索");
    }, 1850);
  });

  toggleMailboxButton.addEventListener("click", function () {
    if (mailboxEnabled) {
      openDialog(disableDialog);
      return;
    }
    mailboxEnabled = true;
    mailboxActiveBadge.classList.remove("is-disabled");
    mailboxActiveBadge.innerHTML = "<i></i>运行中";
    toggleMailboxButton.classList.remove("is-restore");
    toggleMailboxButton.textContent = "停用此邮箱";
    manualSyncButton.disabled = false;
    document.getElementById("scheduleStatus").textContent = "已开启 · 每2小时";
    document.getElementById("nextSyncAt").textContent = "下次执行：今天 11:00";
    document.getElementById("connectionStatusText").innerHTML = "<i></i>连接正常";
    showToast("邮箱已恢复，自动同步将继续运行");
  });

  document.getElementById("confirmDisableButton").addEventListener("click", function () {
    mailboxEnabled = false;
    closeDialog(disableDialog);
    mailboxActiveBadge.classList.add("is-disabled");
    mailboxActiveBadge.innerHTML = "<i></i>已停用";
    toggleMailboxButton.classList.add("is-restore");
    toggleMailboxButton.textContent = "恢复此邮箱";
    manualSyncButton.disabled = true;
    document.getElementById("scheduleStatus").textContent = "已停用";
    document.getElementById("nextSyncAt").textContent = "自动同步已暂停";
    document.getElementById("connectionStatusText").innerHTML = "<i></i>邮箱已停用";
    showToast("邮箱已停用，历史同步记录仍会保留");
  });

  const profileButton = document.getElementById("profileButton");
  const profileMenu = document.getElementById("profileMenu");

  profileButton.addEventListener("click", function () {
    const expanded = profileButton.getAttribute("aria-expanded") === "true";
    profileButton.setAttribute("aria-expanded", String(!expanded));
    profileMenu.hidden = expanded;
  });

  profileMenu.addEventListener("click", function (event) {
    if (event.target.dataset.profileAction === "password") {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
      showToast("密码修改入口已打开");
    }
  });

  document.getElementById("agentEntry").addEventListener("click", function () {
    showToast("Agent 智能对话可返回风险看板后使用");
  });

  document.getElementById("noticeButton").addEventListener("click", function () {
    showToast("3条待处理提醒：2项高风险、1项回款节点");
  });

  document.addEventListener("click", function (event) {
    if (!event.target.closest(".mail-select")) closeSelects(null);
    if (!profileButton.contains(event.target) && !profileMenu.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    closeSelects(null);
    const openDialogElement = Array.from(document.querySelectorAll(".mail-dialog")).find(function (dialog) {
      return !dialog.hidden;
    });
    if (openDialogElement) closeDialog(openDialogElement);
  });
}());
