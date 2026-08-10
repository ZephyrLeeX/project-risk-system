(() => {
  const body = document.body;
  const sidebar = document.querySelector("#adminSidebar");
  const sidebarToggle = document.querySelector("#sidebarToggle");
  const sidebarBackdrop = document.querySelector("#sidebarBackdrop");
  const profileButton = document.querySelector("#adminProfileButton");
  const profileMenu = document.querySelector("#adminProfileMenu");
  const noticeButton = document.querySelector("#noticeButton");
  const toast = document.querySelector("#configToast");
  const toastCopy = toast?.querySelector("p");
  const unsavedBar = document.querySelector("#unsavedBar");
  const unsavedCount = document.querySelector("#unsavedCount");
  const saveAllButton = document.querySelector("#saveAllButton");
  const publishChangesButton = document.querySelector("#publishChangesButton");
  const publishConfigModal = document.querySelector("#publishConfigModal");
  const confirmPublishConfigButton = document.querySelector("#confirmPublishConfigButton");
  const discardModal = document.querySelector("#discardModal");
  const categoryDrawer = document.querySelector("#categoryDrawer");
  const aliasDrawer = document.querySelector("#aliasDrawer");
  const historyDetailModal = document.querySelector("#historyDetailModal");
  const categoryList = document.querySelector("#categoryList");
  const categorySearch = document.querySelector("#categorySearch");
  const categoryEmpty = document.querySelector("#categoryEmpty");
  const categoryForm = document.querySelector("#categoryForm");
  const categoryDrawerEyebrow = document.querySelector("#categoryDrawerEyebrow");
  const categoryDrawerTitle = document.querySelector("#categoryDrawerTitle");
  const categoryNameInput = document.querySelector("#categoryNameInput");
  const categoryCodeInput = document.querySelector("#categoryCodeInput");
  const categoryColorInput = document.querySelector("#categoryColorInput");
  const categoryKeywordsInput = document.querySelector("#categoryKeywordsInput");
  const categoryDescriptionInput = document.querySelector("#categoryDescriptionInput");
  const aliasForm = document.querySelector("#aliasForm");
  const aliasDrawerEyebrow = document.querySelector("#aliasDrawerEyebrow");
  const aliasDrawerTitle = document.querySelector("#aliasDrawerTitle");
  const aliasProjectInput = document.querySelector("#aliasProjectInput");
  const aliasNamesInput = document.querySelector("#aliasNamesInput");
  const aliasNoteInput = document.querySelector("#aliasNoteInput");
  const aliasTableBody = document.querySelector("#aliasTableBody");
  const aliasSearch = document.querySelector("#aliasSearch");
  const aliasEmpty = document.querySelector("#aliasEmpty");

  let changeCount = 0;
  let toastTimer = null;
  let categoryMode = "create";
  let editingCategory = null;
  let aliasMode = "create";
  let editingAliasRow = null;

  const overlayElements = [
    categoryDrawer,
    aliasDrawer,
    publishConfigModal,
    historyDetailModal,
    discardModal
  ];

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
      "config-overlay-open",
      overlayElements.some((element) => element && !element.hidden)
    );
  };

  const openOverlay = (element) => {
    if (!element) return;
    element.hidden = false;
    syncOverlayState();
    window.setTimeout(() => {
      element.querySelector("input:not([disabled]), button:not([disabled])")?.focus();
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

  const markChange = (source = null) => {
    if (source?.dataset.dirty === "true") return;
    if (source) source.dataset.dirty = "true";
    changeCount += 1;
    unsavedCount.textContent = String(changeCount);
    unsavedBar.hidden = false;
    saveAllButton.disabled = false;
  };

  const clearChanges = () => {
    changeCount = 0;
    unsavedCount.textContent = "0";
    unsavedBar.hidden = true;
    saveAllButton.disabled = true;
    document.querySelectorAll("[data-dirty='true']").forEach((element) => {
      delete element.dataset.dirty;
    });
  };

  const openSection = (sectionName) => {
    document.querySelectorAll("[data-section-panel]").forEach((panel) => {
      const active = panel.dataset.sectionPanel === sectionName;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    document.querySelectorAll("[data-config-section]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.configSection === sectionName);
    });
    document.querySelector(".config-workspace")?.scrollIntoView({
      behavior: "smooth",
      block: "start"
    });
  };

  const updateCategoryCount = () => {
    const count = categoryList.querySelectorAll("article").length;
    document.querySelector("#categorySummaryCount").innerHTML = `${count}<em>类</em>`;
  };

  const updateAliasCount = () => {
    const count = [...aliasTableBody.querySelectorAll(".alias-chips span")].length;
    document.querySelector("#aliasNavCount").textContent = String(count);
  };

  const filterCategories = () => {
    const keyword = categorySearch.value.trim().toLowerCase();
    let visible = 0;
    categoryList.querySelectorAll("article").forEach((article) => {
      const text = article.textContent.toLowerCase();
      article.hidden = Boolean(keyword) && !text.includes(keyword);
      if (!article.hidden) visible += 1;
    });
    categoryEmpty.hidden = visible > 0;
  };

  const filterAliases = () => {
    const keyword = aliasSearch.value.trim().toLowerCase();
    let visible = 0;
    aliasTableBody.querySelectorAll("tr").forEach((row) => {
      const text = row.textContent.toLowerCase();
      row.hidden = Boolean(keyword) && !text.includes(keyword);
      if (!row.hidden) visible += 1;
    });
    aliasEmpty.hidden = visible > 0;
  };

  const keywordValues = (article) =>
    [...article.querySelectorAll(".keyword-list span")].map((span) => span.textContent.trim());

  const renderKeywordSpans = (container, values) => {
    container.replaceChildren();
    values.filter(Boolean).forEach((value) => {
      const span = document.createElement("span");
      span.textContent = value;
      container.append(span);
    });
  };

  const openCategoryDrawer = (article = null) => {
    editingCategory = article;
    categoryMode = article ? "edit" : "create";
    categoryForm.reset();
    categoryColorInput.value = "#4C8FE8";
    categoryDrawerEyebrow.textContent = article ? "EDIT RISK CATEGORY" : "NEW RISK CATEGORY";
    categoryDrawerTitle.textContent = article ? "编辑风险类别" : "新增风险类别";
    categoryCodeInput.disabled = Boolean(article);

    if (article) {
      categoryNameInput.value = article.querySelector(".category-copy strong").textContent;
      categoryCodeInput.value = article.dataset.categoryCode;
      categoryKeywordsInput.value = keywordValues(article).join("，");
      const markColor = getComputedStyle(article.querySelector(".category-mark")).backgroundColor;
      const match = markColor.match(/\d+/g);
      if (match) {
        categoryColorInput.value = `#${match.slice(0, 3).map((value) => Number(value).toString(16).padStart(2, "0")).join("")}`;
      }
      categoryDescriptionInput.value = "用于项目周报、日常上报和AI候选的风险分类。";
    }
    openOverlay(categoryDrawer);
    categoryNameInput.focus();
  };

  const createCategoryArticle = () => {
    const template = categoryList.querySelector("article:last-child").cloneNode(true);
    delete template.dataset.dirty;
    template.hidden = false;
    template.dataset.categoryStatus = "ACTIVE";
    template.querySelector(".item-status").className = "item-status is-active";
    template.querySelector(".item-status").innerHTML = "<i></i>启用";
    template.querySelector("[data-toggle-category]").textContent = "停用";
    categoryList.append(template);
    return template;
  };

  const saveCategoryDraft = () => {
    const article = editingCategory || createCategoryArticle();
    const name = categoryNameInput.value.trim();
    const code = categoryCodeInput.value.trim().toUpperCase().replace(/\s+/g, "_");
    const keywords = categoryKeywordsInput.value
      .split(/[，,]/)
      .map((item) => item.trim())
      .filter(Boolean);
    article.dataset.categoryCode = code;
    article.querySelector(".category-copy strong").textContent = name;
    article.querySelector(".category-copy small").textContent = `${code} · ${editingCategory ? "已有关联风险" : "尚未关联风险"}`;
    article.querySelector(".category-mark").className = "category-mark";
    article.querySelector(".category-mark").style.backgroundColor = categoryColorInput.value;
    renderKeywordSpans(article.querySelector(".keyword-list"), keywords);
    markChange(article);
    updateCategoryCount();
    closeOverlay(categoryDrawer);
    filterCategories();
    showToast(`风险类别“${name}”已保存到配置草稿。`);
  };

  const openAliasDrawer = (row = null) => {
    editingAliasRow = row;
    aliasMode = row ? "edit" : "create";
    aliasForm.reset();
    aliasDrawerEyebrow.textContent = row ? "EDIT PROJECT ALIAS" : "NEW PROJECT ALIAS";
    aliasDrawerTitle.textContent = row ? "编辑项目别名" : "新增项目别名";
    if (row) {
      aliasProjectInput.value = row.cells[0].querySelector("strong").textContent;
      aliasNamesInput.value = [...row.querySelectorAll(".alias-chips span")]
        .map((span) => span.textContent)
        .join("，");
      aliasNoteInput.value = "用于周报项目名称匹配。";
    }
    openOverlay(aliasDrawer);
    aliasProjectInput.focus();
  };

  const createAliasRow = () => {
    const row = aliasTableBody.querySelector("tr:last-child").cloneNode(true);
    delete row.dataset.dirty;
    row.hidden = false;
    row.cells[1].innerHTML = '<span class="empty-code">未提供</span>';
    row.cells[3].textContent = "系统管理员";
    row.cells[4].textContent = "草稿 · 0次";
    aliasTableBody.append(row);
    return row;
  };

  const saveAliasDraft = () => {
    const row = editingAliasRow || createAliasRow();
    const project = aliasProjectInput.value.trim();
    const aliases = aliasNamesInput.value
      .split(/[，,]/)
      .map((item) => item.trim())
      .filter(Boolean);
    row.cells[0].querySelector("strong").textContent = project;
    row.cells[0].querySelector("small").textContent = editingAliasRow
      ? row.cells[0].querySelector("small").textContent
      : "负责人：待匹配";
    renderKeywordSpans(row.querySelector(".alias-chips"), aliases);
    markChange(row);
    updateAliasCount();
    closeOverlay(aliasDrawer);
    filterAliases();
    showToast(`“${project}”的项目别名已保存到配置草稿。`);
  };

  const addEditableChip = (list, input, counter) => {
    const value = input.value.trim();
    if (!value) return;
    const duplicate = [...list.querySelectorAll(":scope > span")].some(
      (chip) => chip.firstChild.textContent.trim() === value
    );
    if (duplicate) {
      showToast(`关键词“${value}”已存在。`);
      return;
    }
    const chip = document.createElement("span");
    chip.append(document.createTextNode(value));
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.setAttribute("aria-label", `删除${value}`);
    removeButton.textContent = "×";
    chip.append(removeButton);
    list.insertBefore(chip, list.querySelector("label"));
    input.value = "";
    markChange(list);
    counter.textContent = `${list.querySelectorAll(":scope > span").length}个关键词`;
  };

  const openPublishModal = () => {
    if (!changeCount) {
      showToast("当前没有未发布的配置更改。");
      return;
    }
    document.querySelector("#publishChangeCount").textContent = `${changeCount}项`;
    openOverlay(publishConfigModal);
  };

  const publishChanges = () => {
    confirmPublishConfigButton.classList.add("is-loading");
    confirmPublishConfigButton.disabled = true;
    window.setTimeout(() => {
      confirmPublishConfigButton.classList.remove("is-loading");
      confirmPublishConfigButton.disabled = false;
      closeOverlay(publishConfigModal);
      clearChanges();
      document.querySelector(".secure-status").lastChild.textContent = " 配置版本 V12.4";
      document.querySelector("#directoryVersion").textContent = "V12.4 · 刚刚发布";
      document.querySelector(".version-card > header > span").textContent = "V12.4";
      document.querySelector(".version-card dl dd").textContent = "2026-07-24 刚刚";
      document.querySelector("#overviewSection .section-heading > div > span").textContent = "当前生效版本 V12.4";
      showToast("系统配置已发布，版本已由V12.3更新为V12.4。");
    }, 850);
  };

  const filterHistory = (moduleName) => {
    let visible = 0;
    document.querySelectorAll("#configHistoryBody tr").forEach((row) => {
      row.hidden = moduleName !== "all" && row.dataset.historyModule !== moduleName;
      if (!row.hidden) visible += 1;
    });
    document.querySelector("#historyEmpty").hidden = visible > 0;
  };

  const openHistoryDetail = (row) => {
    const cells = row.cells;
    document.querySelector("#detailVersion").textContent = cells[0].querySelector("strong").textContent;
    document.querySelector("#detailVersionMeta").textContent = `2026-${cells[0].querySelector("small").textContent} · ${cells[3].textContent.trim()}`;
    document.querySelector("#detailBefore").textContent = "变更前配置按上一版本规则执行，历史风险与任务记录保持原值。";
    document.querySelector("#detailAfter").textContent = cells[2].textContent.trim();
    openOverlay(historyDetailModal);
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
    showToast("账号安全：当前会话具备admin.config.manage权限。");
  });

  noticeButton.addEventListener("click", () => {
    showToast("后台提醒：1个API Key即将到期，1个Excel批次仍有待确认记录。");
  });

  document.querySelectorAll("[data-next-module]").forEach((button) => {
    button.addEventListener("click", () => {
      showToast(`${button.dataset.nextModule}将在后续页面中逐页生成。`);
    });
  });

  document.querySelectorAll("[data-config-section]").forEach((button) => {
    button.addEventListener("click", () => openSection(button.dataset.configSection));
  });

  document.querySelectorAll("[data-open-section]").forEach((button) => {
    button.addEventListener("click", () => openSection(button.dataset.openSection));
  });

  document.querySelector("#viewHistoryButton").addEventListener("click", () => openSection("history"));

  document.querySelector("#addCategoryButton").addEventListener("click", () => openCategoryDrawer());
  categorySearch.addEventListener("input", filterCategories);
  categoryForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveCategoryDraft();
  });

  categoryList.addEventListener("click", (event) => {
    const article = event.target.closest("article");
    if (!article) return;
    if (event.target.closest("[data-edit-category]")) openCategoryDrawer(article);
    if (event.target.closest("[data-toggle-category]")) {
      const enabling = article.dataset.categoryStatus !== "ACTIVE";
      article.dataset.categoryStatus = enabling ? "ACTIVE" : "DISABLED";
      const status = article.querySelector(".item-status");
      status.className = `item-status ${enabling ? "is-active" : "is-disabled"}`;
      status.innerHTML = `<i></i>${enabling ? "启用" : "停用"}`;
      event.target.textContent = enabling ? "停用" : "启用";
      markChange(article);
      showToast(`“${article.querySelector(".category-copy strong").textContent}”已${enabling ? "启用" : "停用"}，发布后生效。`);
    }
    if (event.target.closest("[data-move-up]") && article.previousElementSibling) {
      categoryList.insertBefore(article, article.previousElementSibling);
      markChange(categoryList);
    }
    if (event.target.closest("[data-move-down]") && article.nextElementSibling) {
      categoryList.insertBefore(article.nextElementSibling, article);
      markChange(categoryList);
    }
  });

  document.querySelectorAll("[data-level-color]").forEach((button) => {
    button.addEventListener("click", () => {
      const article = button.closest("article");
      const palettes = {
        HIGH: ["#EF5555", "#D94747", "#F06A4F"],
        MEDIUM: ["#F0A019", "#D9890B", "#E7B326"],
        LOW: ["#21A66D", "#168B5A", "#2AA187"]
      };
      const code = article.querySelector("code").textContent;
      const current = button.dataset.levelColor.toUpperCase();
      const options = palettes[code];
      const next = options[(options.indexOf(current) + 1) % options.length];
      button.dataset.levelColor = next;
      article.style.setProperty("--level-color", next);
      markChange(button);
      showToast(`${article.querySelector("header span").textContent}颜色已调整，发布后生效。`);
    });
  });

  document.querySelectorAll("[data-sync-interval]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-sync-interval]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      markChange(document.querySelector(".choice-group"));
      showToast(`邮箱自动同步周期已调整为${button.textContent.trim()}。`);
    });
  });

  const subjectList = document.querySelector("#subjectKeywordList");
  const riskList = document.querySelector("#riskKeywordList");
  const subjectInput = document.querySelector("#subjectKeywordInput");
  const riskInput = document.querySelector("#riskKeywordInput");
  const subjectCounter = document.querySelector("#subjectKeywordCount");
  const riskCounter = document.querySelector("#riskKeywordCount");

  [
    [subjectList, subjectCounter],
    [riskList, riskCounter]
  ].forEach(([list, counter]) => {
    list.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button || button.closest(".editable-chip-list") !== list) return;
      button.closest("span").remove();
      markChange(list);
      counter.textContent = `${list.querySelectorAll(":scope > span").length}个关键词`;
    });
  });

  subjectInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addEditableChip(subjectList, subjectInput, subjectCounter);
  });

  riskInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addEditableChip(riskList, riskInput, riskCounter);
  });

  document.querySelector("#addAliasButton").addEventListener("click", () => openAliasDrawer());
  aliasSearch.addEventListener("input", filterAliases);
  aliasTableBody.addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit-alias]");
    if (button) openAliasDrawer(button.closest("tr"));
  });
  aliasForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveAliasDraft();
  });

  document.querySelectorAll("[data-track-change]").forEach((element) => {
    element.addEventListener("change", () => markChange(element));
  });

  document.querySelectorAll("[data-history-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-history-filter]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      filterHistory(button.dataset.historyFilter);
    });
  });

  document.querySelectorAll("[data-history-detail]").forEach((button) => {
    button.addEventListener("click", () => openHistoryDetail(button.closest("tr")));
  });

  saveAllButton.addEventListener("click", openPublishModal);
  publishChangesButton.addEventListener("click", openPublishModal);
  confirmPublishConfigButton.addEventListener("click", publishChanges);
  document.querySelector("#discardChangesButton").addEventListener("click", () => openOverlay(discardModal));
  document.querySelector("#confirmDiscardButton").addEventListener("click", () => {
    clearChanges();
    window.location.reload();
  });

  document.querySelectorAll("[data-close-category-drawer]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(categoryDrawer));
  });
  document.querySelectorAll("[data-close-alias-drawer]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(aliasDrawer));
  });
  document.querySelectorAll("[data-close-publish-config]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(publishConfigModal));
  });
  document.querySelectorAll("[data-close-history-detail]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(historyDetailModal));
  });
  document.querySelectorAll("[data-close-discard]").forEach((button) => {
    button.addEventListener("click", () => closeOverlay(discardModal));
  });

  document.addEventListener("click", (event) => {
    if (!profileMenu.hidden && !profileMenu.contains(event.target) && !profileButton.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const openElement = [...overlayElements].reverse().find((element) => !element.hidden);
    if (openElement) {
      closeOverlay(openElement);
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

  window.addEventListener("beforeunload", (event) => {
    if (!changeCount) return;
    event.preventDefault();
    event.returnValue = "";
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 1040) closeSidebar();
  });

  updateCategoryCount();
  updateAliasCount();
})();
