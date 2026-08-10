(function () {
  "use strict";

  if (document.body.dataset.page !== "dashboard") {
    return;
  }

  const riskItems = [
    {
      id: 1,
      level: "高风险",
      project: "锡山智慧城市一期",
      owner: "王绍华",
      category: "供应商风险",
      description: "供应商核减谈判陷入僵局：电信、云联、启明、浪潮均不同意任何核减。",
      reporter: "刘峰",
      source: "周报AI提炼",
      status: "持续",
      week: "第27周",
      evidence: "电信：不同意任何核减；云联：不同意任何核减；启明：不同意任何核减；浪潮：不肯谈，诉讼中。",
      suggestion: "如近期数据局无新方案，建议先发起投诉；如果投诉后得不到良好解决，建议起诉。"
    },
    {
      id: 2,
      level: "高风险",
      project: "锡山智慧城市一期",
      owner: "王绍华",
      category: "回款风险",
      description: "终验款592.64万在途，供应商核减报价博弈中；甲方要求我方汇总报价，只有一次报价机会。",
      reporter: "刘峰",
      source: "日常上报",
      status: "新增",
      week: "第27周",
      evidence: "甲方反复强调顾书记已和我司集团领导达成一致处理意见，待核实。",
      suggestion: "核实顾书记与集团领导沟通结果，准备7月中旬最终报价。"
    },
    {
      id: 3,
      level: "高风险",
      project: "锡山智慧城市一期",
      owner: "王绍华",
      category: "客户层面风险",
      description: "客户要求统一由我方汇总提交报价，涉及法务和经营决策。",
      reporter: "刘峰",
      source: "周报AI提炼",
      status: "新增",
      week: "第27周",
      evidence: "甲方要求：统一由我方汇总提交报价；如实报价，不要期望来回拉扯。",
      suggestion: "管理者需决策是否启动法律程序。"
    },
    {
      id: 4,
      level: "高风险",
      project: "新吴城运数字底座",
      owner: "付瑞强",
      category: "回款风险",
      description: "第一年质保款551.67万已延期，新吴财政状况不理想。",
      reporter: "刘峰",
      source: "周报AI提炼",
      status: "持续",
      week: "第27周",
      evidence: "经开区并入新吴区，新吴财政状况更加不理想，为求回款建议有既定计划请尽快做出动作。",
      suggestion: "尽早按既定计划推进回款动作。"
    },
    {
      id: 5,
      level: "中风险",
      project: "新吴城运数字底座",
      owner: "付瑞强",
      category: "客户层面风险",
      description: "新吴区财政状况不理想，存在长期拖欠风险。",
      reporter: "刘峰",
      source: "周报AI提炼",
      status: "新增",
      week: "第27周",
      evidence: "经开区并入新吴区，新吴财政状况更加不理想。",
      suggestion: "持续关注新吴财政情况，做好风险预案。"
    },
    {
      id: 6,
      level: "中风险",
      project: "市数据局综合事务系统",
      owner: "刘峰",
      category: "超出合同需求",
      description: "市数据局查主任要求综合事务一、二期系统配合审计，项目已完结且已全部回款。",
      reporter: "刘峰",
      source: "日常上报",
      status: "新增",
      week: "第27周",
      evidence: "查主任表示要直接联系晓刚总来协调此次配合。",
      suggestion: "不建议继续投入支持，需与查主任和晓刚总沟通。"
    },
    {
      id: 7,
      level: "中风险",
      project: "昆山鹿路通APP",
      owner: "肖杰",
      category: "回款风险",
      description: "合同五（350万）已验收预计8月底回款，合同六（93万）预计10月底回款。",
      reporter: "肖杰",
      source: "周报AI提炼",
      status: "持续",
      week: "第27周",
      evidence: "已验收/已签合同状态明确，有明确回款时间。",
      suggestion: "跟踪新招标项目（345万）7月16日开标结果。"
    },
    {
      id: 8,
      level: "中风险",
      project: "昆山鹿路通APP",
      owner: "肖杰",
      category: "成本风险",
      description: "整体已入账成本471.8万，毛利率约35%。",
      reporter: "肖杰",
      source: "周报AI提炼",
      status: "新增",
      week: "第27周",
      evidence: "合同一150万成本毛利率19.35%，合同三47万成本毛利率44.71%。",
      suggestion: "关注合同一毛利率偏低问题。"
    },
    {
      id: 9,
      level: "低风险",
      project: "无锡市应急项目群",
      owner: "赵振兴",
      category: "回款风险",
      description: "市应急、梁溪应急二期、宜兴应急、宜兴城运等项目正常推进中。",
      reporter: "赵振兴",
      source: "周报AI提炼",
      status: "持续",
      week: "第27周",
      evidence: "多个项目并行交付，人力投入稳定。",
      suggestion: "按项目节点维护回款计划。"
    },
    {
      id: 10,
      level: "低风险",
      project: "荆州公司项目群",
      owner: "田雷",
      category: "回款风险",
      description: "2026年计划回款2161.58万元，截至本周实际回款126.05万元。",
      reporter: "田雷",
      source: "周报AI提炼",
      status: "持续",
      week: "第27周",
      evidence: "完成率约5.8%，2026年人力投入需持续关注。",
      suggestion: "加快验收进度并推动回款。"
    },
    {
      id: 11,
      level: "低风险",
      project: "锡东先导区CIM平台",
      owner: "张哲",
      category: "回款风险",
      description: "硬件总金额45.68万，回款进度正常，有具体回款比例表。",
      reporter: "张哲",
      source: "周报AI提炼",
      status: "持续",
      week: "第27周",
      evidence: "按回款阶段逐笔推进。",
      suggestion: "按计划节点逐笔推进。"
    },
    {
      id: 12,
      level: "低风险",
      project: "惠山数字底座",
      owner: "张哲",
      category: "回款风险",
      description: "项目回款按阶段正常推进。",
      reporter: "张哲",
      source: "周报AI提炼",
      status: "持续",
      week: "第27周",
      evidence: "现有回款节点无明显异常。",
      suggestion: "保持正常回款节奏。"
    }
  ];

  const departmentItems = [
    { department: "数据合并", plan: 15267.24, collected: 2089.66, remaining: 13177.57 },
    { department: "项目交付一部", plan: 6875.32, collected: 1192.10, remaining: 5683.22 },
    { department: "项目交付二部", plan: 6132.24, collected: 673.41, remaining: 5458.83 },
    { department: "数字荆州", plan: 2161.58, collected: 126.05, remaining: 2035.53 },
    { department: "朗新数能", plan: 77.82, collected: 77.82, remaining: 0 },
    { department: "云筑", plan: 18.14, collected: 18.14, remaining: 0 },
    { department: "售电", plan: 2.14, collected: 2.14, remaining: 0 },
    { department: "涵谷", plan: 748.15, collected: 206.00, remaining: 542.15 }
  ];

  const todoItems = [
    { urgency: "紧急", project: "锡山智慧城市一期", task: "核实顾书记与集团领导沟通结果", owner: "王绍华", type: "客户沟通" },
    { urgency: "紧急", project: "锡山智慧城市一期", task: "决策是否对供应商发起投诉或诉讼", owner: "管理者", type: "法务决策" },
    { urgency: "高", project: "锡山智慧城市一期", task: "准备7月中旬提交甲方的最终报价", owner: "刘峰", type: "商务报价" },
    { urgency: "高", project: "新吴城运数字底座", task: "按既定计划推进回款动作", owner: "付瑞强", type: "回款决策" },
    { urgency: "中", project: "市数据局综合事务系统", task: "沟通审计配合事项及工作边界", owner: "管理者", type: "关系对接" },
    { urgency: "中", project: "昆山鹿路通APP", task: "跟踪345万新增项目开标结果", owner: "肖杰", type: "商务跟踪" },
    { urgency: "中", project: "市数据局综合事务系统", task: "梳理审计所需材料清单", owner: "刘峰", type: "技术支持" },
    { urgency: "中", project: "荆州公司项目群", task: "确认下一批验收和回款节点", owner: "田雷", type: "回款跟踪" }
  ];

  const paymentItems = [
    { project: "锡山智慧城市一期", plan: 5926.40, collected: 2933.28, remaining: 592.64, next: "7月 · 终验款592.64万" },
    { project: "新吴城运数字底座", plan: 3677.80, collected: 1838.90, remaining: 919.45, next: "质保款551.67万 + 367.78万" },
    { project: "昆山鹿路通APP", plan: 805.00, collected: 362.00, remaining: 443.00, next: "8月 · 合同五350万" },
    { project: "荆州公司项目群", plan: 2161.58, collected: 126.05, remaining: 2035.53, next: "按项目验收节点推进" },
    { project: "无锡市应急项目群", plan: 1090.00, collected: 0, remaining: 1090.00, next: "推进初验回款" },
    { project: "锡东先导区CIM平台", plan: 45.68, collected: 18.27, remaining: 27.41, next: "硬件阶段款" }
  ];

  const timelineItems = [
    { date: "2026-07-23 09:18", level: "高风险", status: "持续", title: "锡山供应商核减僵局", description: "多家供应商仍不同意核减，等待管理决策。" },
    { date: "2026-07-22 16:40", level: "高风险", status: "新增", title: "新吴质保款延期", description: "第一年质保款551.67万未按计划到账。" },
    { date: "2026-07-21 14:06", level: "中风险", status: "新增", title: "市数据局审计要求", description: "已完结项目被要求继续配合审计。" },
    { date: "2026-07-20 11:32", level: "中风险", status: "持续", title: "昆山鹿路通新项目招标", description: "新增功能项目7月16日开标，持续跟踪结果。" },
    { date: "2026-07-18 17:45", level: "低风险", status: "持续", title: "荆州公司回款统计", description: "年度回款完成率5.8%，需推进验收。" },
    { date: "2026-07-17 10:20", level: "高风险", status: "等级上调", title: "锡山甲方沟通压力", description: "甲方强调集团层面已有处理意见，待进一步核实。" },
    { date: "2026-07-15 15:10", level: "中风险", status: "缓解", title: "新吴数据共享推进", description: "统一身份认证和工单数据共享推送已完成。" }
  ];

  const resolvedItems = [
    {
      description: "多项验收回款风险已通过节点推进解决",
      project: "无锡市应急项目群",
      level: "低风险",
      category: "验收延期",
      date: "2026-07-08",
      reason: "验收推进顺利，回款节点已明确。"
    },
    {
      description: "统一身份认证和工单数据共享推送已完成",
      project: "新吴城运数字底座",
      level: "中风险",
      category: "超出合同需求",
      date: "2026-07-03",
      reason: "数据共享功能已交付并验收通过。"
    },
    {
      description: "首付款剩余3.2万已全部到账",
      project: "无锡市政务外网三期",
      level: "低风险",
      category: "回款风险",
      date: "2026-07-09",
      reason: "客户资金到位，剩余回款已收到。"
    }
  ];

  const reports = [
    {
      sender: "刘峰",
      date: "2026-07-05",
      projects: "锡山智慧城市一期、新吴城运数字底座等4项",
      projectNames: [
        "锡山智慧城市一期",
        "新吴城运数字底座",
        "无锡市政务外网三期",
        "市数据局综合事务"
      ],
      points: "供应商核减谈判陷入僵局；新吴质保款延期；市数据局要求配合审计。"
    },
    {
      sender: "肖杰",
      date: "2026-07-03",
      projects: "昆山鹿路通APP",
      projectNames: ["昆山鹿路通APP"],
      points: "新增功能项目招标进行中；APP 5.1.0版本开发中。"
    },
    {
      sender: "赵振兴",
      date: "2026-07-03",
      projects: "无锡市应急项目群等5项",
      projectNames: [
        "无锡市应急项目",
        "梁溪应急二期",
        "宜兴应急",
        "宜兴城运",
        "东亭街道充电桩"
      ],
      points: "多个项目正常推进，部分项目进入回款阶段。"
    },
    {
      sender: "田雷",
      date: "2026-07-04",
      projects: "荆州公司项目群",
      projectNames: ["荆州公司项目群"],
      points: "年度计划回款2161.58万，实际回款126.05万。"
    },
    {
      sender: "张哲",
      date: "2026-07-05",
      projects: "锡东先导区CIM平台、惠山数字底座",
      projectNames: ["锡东先导区CIM平台", "惠山数字底座"],
      points: "硬件总金额45.68万，回款按阶段正常推进。"
    }
  ];

  const elements = {
    riskTable: document.getElementById("riskTable"),
    riskEmpty: document.getElementById("riskEmpty"),
    riskResultCount: document.getElementById("riskResultCount"),
    riskSearch: document.getElementById("riskSearch"),
    levelFilter: document.getElementById("levelFilter"),
    categoryFilter: document.getElementById("categoryFilter"),
    ownerFilter: document.getElementById("ownerFilter"),
    departmentTable: document.getElementById("departmentTable"),
    todoTable: document.getElementById("todoTable"),
    todoOwnerFilter: document.getElementById("todoOwnerFilter"),
    paymentTable: document.getElementById("paymentTable"),
    timelineList: document.getElementById("timelineList"),
    resolvedTable: document.getElementById("resolvedTable"),
    reportsTable: document.getElementById("reportsTable"),
    modalOverlay: document.getElementById("modalOverlay"),
    modalKicker: document.getElementById("modalKicker"),
    modalTitle: document.getElementById("modalTitle"),
    modalContent: document.getElementById("modalContent"),
    agentOverlay: document.getElementById("agentOverlay"),
    agentMessages: document.getElementById("agentMessages"),
    toast: document.getElementById("toast")
  };

  let toastTimer;
  let lastFocusedElement;

  function formatMoney(value) {
    return value.toLocaleString("zh-CN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }) + "万";
  }

  function levelClass(level) {
    if (level === "高风险") return "level-high";
    if (level === "中风险") return "level-medium";
    if (level === "无风险") return "level-none";
    return "level-low";
  }

  function urgencyClass(urgency) {
    if (urgency === "紧急") return "urgency-critical";
    if (urgency === "高") return "urgency-high";
    return "urgency-medium";
  }

  function progressMarkup(value) {
    const safeValue = Math.max(0, Math.min(100, value));
    const progressClass = safeValue >= 70 ? "is-good" : safeValue >= 25 ? "is-medium" : "";

    return `
      <div class="progress-cell">
        <div class="progress-meta"><span>完成率</span><strong>${safeValue.toFixed(1)}%</strong></div>
        <div class="progress-track">
          <div class="progress-fill ${progressClass}" style="width:${safeValue}%"></div>
        </div>
      </div>
    `;
  }

  function populateSelect(select, values) {
    values.forEach(function (value) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }

  function closeCustomSelect(wrapper, restoreFocus) {
    if (!wrapper || !wrapper.classList.contains("is-open")) return;
    const trigger = wrapper.querySelector(".custom-select-trigger");
    const menu = wrapper.querySelector(".custom-select-menu");
    wrapper.classList.remove("is-open");
    trigger.setAttribute("aria-expanded", "false");
    menu.hidden = true;
    if (restoreFocus) trigger.focus();
  }

  function closeOtherCustomSelects(currentWrapper) {
    document.querySelectorAll(".custom-select.is-open").forEach(function (wrapper) {
      if (wrapper !== currentWrapper) closeCustomSelect(wrapper, false);
    });
  }

  function enhanceSelect(select) {
    const wrapper = document.createElement("div");
    const trigger = document.createElement("button");
    const triggerLabel = document.createElement("span");
    const arrow = document.createElement("span");
    const menu = document.createElement("ul");
    const menuId = select.id + "CustomMenu";

    wrapper.className = "custom-select" +
      (select.classList.contains("compact-select") ? " compact-custom-select" : "");
    trigger.className = "custom-select-trigger";
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", menuId);
    triggerLabel.className = "custom-select-label";
    arrow.className = "custom-select-arrow";
    arrow.setAttribute("aria-hidden", "true");
    menu.className = "custom-select-menu";
    menu.id = menuId;
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", select.getAttribute("aria-label") || "选择选项");
    menu.hidden = true;

    trigger.append(triggerLabel, arrow);
    wrapper.append(trigger, menu);
    select.classList.add("native-select-hidden");
    select.insertAdjacentElement("afterend", wrapper);

    Array.from(select.options).forEach(function (option) {
      const item = document.createElement("li");
      const optionButton = document.createElement("button");
      optionButton.className = "custom-select-option";
      optionButton.type = "button";
      optionButton.setAttribute("role", "option");
      optionButton.dataset.value = option.value;
      optionButton.textContent = option.textContent;
      item.appendChild(optionButton);
      menu.appendChild(item);
    });

    function syncSelection() {
      const selectedOption = select.options[select.selectedIndex];
      triggerLabel.textContent = selectedOption ? selectedOption.textContent : "请选择";
      trigger.setAttribute(
        "aria-label",
        (select.getAttribute("aria-label") || "选择选项") + "，当前为" + triggerLabel.textContent
      );
      menu.querySelectorAll(".custom-select-option").forEach(function (optionButton) {
        optionButton.setAttribute(
          "aria-selected",
          String(optionButton.dataset.value === select.value)
        );
      });
    }

    function openCustomSelect() {
      closeOtherCustomSelects(wrapper);
      wrapper.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      menu.hidden = false;
    }

    function focusSelectedOption() {
      const selected = menu.querySelector('.custom-select-option[aria-selected="true"]');
      const firstOption = menu.querySelector(".custom-select-option");
      (selected || firstOption)?.focus();
    }

    function moveOptionFocus(currentButton, direction) {
      const buttons = Array.from(menu.querySelectorAll(".custom-select-option"));
      const currentIndex = buttons.indexOf(currentButton);
      const nextIndex = Math.max(0, Math.min(buttons.length - 1, currentIndex + direction));
      buttons[nextIndex]?.focus();
    }

    trigger.addEventListener("click", function () {
      if (wrapper.classList.contains("is-open")) {
        closeCustomSelect(wrapper, false);
      } else {
        openCustomSelect();
      }
    });

    trigger.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        openCustomSelect();
        focusSelectedOption();
      }
    });

    menu.addEventListener("click", function (event) {
      const optionButton = event.target.closest(".custom-select-option");
      if (!optionButton) return;
      select.value = optionButton.dataset.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      syncSelection();
      closeCustomSelect(wrapper, true);
    });

    menu.addEventListener("keydown", function (event) {
      const optionButton = event.target.closest(".custom-select-option");
      if (!optionButton) return;

      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveOptionFocus(optionButton, 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        moveOptionFocus(optionButton, -1);
      } else if (event.key === "Home") {
        event.preventDefault();
        menu.querySelector(".custom-select-option")?.focus();
      } else if (event.key === "End") {
        event.preventDefault();
        const options = menu.querySelectorAll(".custom-select-option");
        options[options.length - 1]?.focus();
      } else if (event.key === "Escape") {
        event.preventDefault();
        closeCustomSelect(wrapper, true);
      } else if (event.key === "Tab") {
        closeCustomSelect(wrapper, false);
      }
    });

    select.addEventListener("change", syncSelection);
    select.syncCustomSelect = syncSelection;
    syncSelection();
  }

  function renderRiskTable() {
    const term = elements.riskSearch.value.trim().toLowerCase();
    const level = elements.levelFilter.value;
    const category = elements.categoryFilter.value;
    const owner = elements.ownerFilter.value;

    const filteredItems = riskItems.filter(function (item) {
      const matchesSearch = !term ||
        item.project.toLowerCase().includes(term) ||
        item.description.toLowerCase().includes(term);
      const matchesLevel = level === "all" || item.level === level;
      const matchesCategory = category === "all" || item.category === category;
      const matchesOwner = owner === "all" || item.owner === owner;

      return matchesSearch && matchesLevel && matchesCategory && matchesOwner;
    });

    elements.riskResultCount.textContent = "共" + filteredItems.length + "条风险";
    elements.riskEmpty.hidden = filteredItems.length > 0;
    elements.riskTable.innerHTML = filteredItems.map(function (item) {
      return `
        <tr tabindex="0" data-risk-id="${item.id}">
          <td class="risk-level-cell" data-label="等级"><span class="level-badge ${levelClass(item.level)}">${item.level}</span></td>
          <td class="project-cell risk-project-cell" data-label="项目"><strong>${item.project}</strong><small>负责人：${item.owner}</small></td>
          <td class="risk-category-cell" data-label="类别"><span>${item.category}</span></td>
          <td class="description-cell risk-description-cell" data-label="风险描述"><strong>${item.description}</strong><small>点击查看证据与建议措施</small></td>
          <td class="person-cell risk-person-cell" data-label="上报人 / 来源"><strong>${item.reporter}</strong><span class="source-badge">${item.source}</span></td>
          <td class="risk-week-cell" data-label="周次">${item.week}</td>
        </tr>
      `;
    }).join("");
  }

  function renderDepartmentTable() {
    elements.departmentTable.innerHTML = departmentItems.map(function (item, index) {
      const rate = item.plan > 0 ? item.collected / item.plan * 100 : 0;

      return `
        <tr tabindex="0" data-department-index="${index}">
          <td class="project-cell"><strong>${item.department}</strong><small>点击查看关联项目</small></td>
          <td>${formatMoney(item.plan)}</td>
          <td>${formatMoney(item.collected)}</td>
          <td>${formatMoney(item.remaining)}</td>
          <td>${progressMarkup(rate)}</td>
        </tr>
      `;
    }).join("");
  }

  function renderTodoTable() {
    const owner = elements.todoOwnerFilter.value;
    const items = todoItems.filter(function (item) {
      return owner === "all" || item.owner === owner;
    });

    elements.todoTable.innerHTML = items.map(function (item, index) {
      const urgencyClass = item.urgency === "紧急"
        ? "urgency-critical"
        : item.urgency === "高"
          ? "urgency-high"
          : "urgency-medium";

      return `
        <tr tabindex="0" data-todo-index="${todoItems.indexOf(item)}">
          <td><span class="urgency-badge ${urgencyClass}">${item.urgency}</span></td>
          <td class="project-cell"><strong>${item.project}</strong></td>
          <td class="description-cell"><strong>${item.task}</strong></td>
          <td>${item.owner}</td>
          <td>${item.type}</td>
        </tr>
      `;
    }).join("");
  }

  function renderPaymentTable() {
    elements.paymentTable.innerHTML = paymentItems.map(function (item, index) {
      const rate = item.plan > 0 ? item.collected / item.plan * 100 : 0;

      return `
        <tr tabindex="0" data-payment-index="${index}">
          <td class="project-cell"><strong>${item.project}</strong><small>风险项目</small></td>
          <td>${formatMoney(item.plan)}</td>
          <td>${formatMoney(item.collected)}</td>
          <td>${formatMoney(item.remaining)}</td>
          <td>${item.next}</td>
          <td>${progressMarkup(rate)}</td>
        </tr>
      `;
    }).join("");
  }

  function renderTimeline() {
    elements.timelineList.innerHTML = timelineItems.map(function (item, index) {
      const color = item.level === "高风险"
        ? "#e35451"
        : item.level === "中风险"
          ? "#e99a0a"
          : "#22a47b";

      return `
        <article class="timeline-item" tabindex="0" data-timeline-index="${index}" style="--timeline-color:${color}">
          <time>${item.date}</time>
          <div>
            <strong>${item.title}</strong>
            <p>${item.description}</p>
          </div>
          <span class="timeline-status">${item.status}</span>
        </article>
      `;
    }).join("");
  }

  function renderResolvedTable() {
    elements.resolvedTable.innerHTML = resolvedItems.map(function (item, index) {
      return `
        <tr tabindex="0" data-resolved-index="${index}">
          <td class="description-cell"><strong>${item.description}</strong></td>
          <td>${item.project}</td>
          <td><span class="level-badge ${levelClass(item.level)}">${item.level}</span></td>
          <td>${item.category}</td>
          <td>${item.date}</td>
          <td>${item.reason}</td>
        </tr>
      `;
    }).join("");
  }

  function renderReports() {
    elements.reportsTable.innerHTML = reports.map(function (item, index) {
      return `
        <tr tabindex="0" data-report-index="${index}">
          <td><span class="sync-badge">分析完成</span></td>
          <td class="person-cell"><strong>${item.sender}</strong><small>邮箱同步</small></td>
          <td>${item.date}</td>
          <td class="project-cell"><strong>${item.projects}</strong></td>
          <td class="description-cell"><strong>${item.points}</strong><small>点击查看邮件摘要与关联风险</small></td>
        </tr>
      `;
    }).join("");
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    elements.toast.textContent = message;
    elements.toast.classList.add("is-visible");
    toastTimer = window.setTimeout(function () {
      elements.toast.classList.remove("is-visible");
    }, 2400);
  }

  function detailRows(rows) {
    return `<div class="detail-grid">${rows.map(function (row) {
      return `<div class="detail-row"><span>${row[0]}</span><strong>${row[1]}</strong></div>`;
    }).join("")}</div>`;
  }

  function openModal(kicker, title, content) {
    if (elements.modalOverlay.hidden) {
      lastFocusedElement = document.activeElement;
    }
    elements.modalKicker.textContent = kicker;
    elements.modalTitle.textContent = title;
    elements.modalContent.innerHTML = content;
    elements.modalOverlay.hidden = false;
    document.body.style.overflow = "hidden";
    document.getElementById("closeModal").focus();
  }

  function closeModal() {
    elements.modalOverlay.hidden = true;
    document.body.style.overflow = "";
    if (lastFocusedElement) lastFocusedElement.focus();
  }

  function openRiskDetail(id) {
    const item = riskItems.find(function (risk) {
      return risk.id === Number(id);
    });

    if (!item) return;

    const actions = todoItems.filter(function (action) {
      return action.project === item.project;
    });
    const relatedRisks = riskItems.filter(function (risk) {
      return risk.project === item.project && risk.id !== item.id;
    });
    const actionRows = actions.length
      ? actions.map(function (action) {
        return `
          <tr>
            <td><span class="urgency-badge ${urgencyClass(action.urgency)}">${action.urgency}</span></td>
            <td>${action.task}</td>
            <td>${action.owner}</td>
            <td>${action.type}</td>
          </tr>
        `;
      }).join("")
      : `
        <tr class="risk-detail-empty-row">
          <td colspan="4">当前风险暂无关联行动项</td>
        </tr>
      `;
    const relatedRiskCards = relatedRisks.length
      ? relatedRisks.map(function (risk) {
        return `
          <button class="related-risk-card" type="button" data-related-risk-id="${risk.id}">
            <span class="level-badge ${levelClass(risk.level)}">${risk.level}</span>
            <span>
              <strong>${risk.category}</strong>
              <small>${risk.description}</small>
            </span>
            <i aria-hidden="true"></i>
          </button>
        `;
      }).join("")
      : `<p class="risk-detail-empty">当前项目暂无其他风险。</p>`;

    openModal(
      "RISK DETAIL · " + item.project,
      "风险详情 #" + item.id,
      `
        <div class="risk-detail-meta">
          <span><b>来源</b>${item.reporter} · ${item.source}</span>
          <span><b>周次</b>${item.week}</span>
          <span><b>状态</b>${item.status}</span>
          <span><b>更新时间</b>2026-07-23 09:30</span>
        </div>

        <div class="risk-summary-grid">
          <div>
            <span>风险等级</span>
            <strong class="risk-summary-level ${levelClass(item.level)}">${item.level}</strong>
          </div>
          <div>
            <span>关联项目</span>
            <strong>${item.project}</strong>
          </div>
          <div>
            <span>风险类别</span>
            <strong>${item.category}</strong>
          </div>
          <div>
            <span>上报人</span>
            <strong>${item.reporter}</strong>
          </div>
        </div>

        <section class="risk-detail-block risk-description-block">
          <h3><span class="risk-block-icon risk-description-icon" aria-hidden="true"></span>风险描述</h3>
          <p>${item.description}</p>
        </section>

        <section class="risk-detail-block risk-evidence-block">
          <h3><span class="risk-block-icon risk-evidence-icon" aria-hidden="true"></span>证据 / 信息来源</h3>
          <p>${item.evidence}</p>
        </section>

        <section class="risk-detail-block risk-suggestion-block">
          <h3><span class="risk-block-icon risk-suggestion-icon" aria-hidden="true"></span>建议措施</h3>
          <p>${item.suggestion}</p>
        </section>

        <section class="risk-related-section">
          <div class="risk-section-title">
            <span class="risk-section-symbol risk-action-symbol" aria-hidden="true"></span>
            <div>
              <h3>关联行动项</h3>
              <p>由该项目风险建议形成的待办事项</p>
            </div>
          </div>
          <div class="table-scroll">
            <table class="risk-action-table">
              <thead>
                <tr>
                  <th>紧急度</th>
                  <th>事项</th>
                  <th>负责人</th>
                  <th>类型</th>
                </tr>
              </thead>
              <tbody>${actionRows}</tbody>
            </table>
          </div>
        </section>

        <section class="risk-related-section">
          <div class="risk-section-title">
            <span class="risk-section-symbol risk-link-symbol" aria-hidden="true"></span>
            <div>
              <h3>同项目其他风险</h3>
              <p>点击卡片可继续查看完整风险详情</p>
            </div>
          </div>
          <div class="related-risk-list">${relatedRiskCards}</div>
        </section>

        <footer class="risk-detail-footer">
          <span>数据来源：${item.source}、项目清单Excel及关联回款数据</span>
          <span>风险编号：RISK-${String(item.id).padStart(4, "0")}</span>
        </footer>
      `
    );
  }

  function openReportDetail(index) {
    const report = reports[Number(index)];
    if (!report) return;

    const extractedRisks = riskItems.filter(function (risk) {
      return risk.reporter === report.sender;
    });
    const relatedActions = todoItems.filter(function (action) {
      return report.projectNames.some(function (projectName) {
        return action.project.includes(projectName) || projectName.includes(action.project);
      });
    });
    const riskRank = {
      "无风险": 0,
      "低风险": 1,
      "中风险": 2,
      "高风险": 3
    };
    const projectRows = report.projectNames.map(function (projectName) {
      const projectRisks = riskItems.filter(function (risk) {
        return risk.project.includes(projectName) || projectName.includes(risk.project);
      });
      const projectStatus = projectRisks.reduce(function (currentLevel, risk) {
        return riskRank[risk.level] > riskRank[currentLevel] ? risk.level : currentLevel;
      }, "无风险");

      return `
        <tr>
          <td>${projectName}</td>
          <td><span class="level-badge ${levelClass(projectStatus)}">${projectStatus}</span></td>
        </tr>
      `;
    }).join("");
    const riskCards = extractedRisks.length
      ? extractedRisks.map(function (risk) {
        return `
          <button class="report-risk-card" type="button" data-report-risk-id="${risk.id}">
            <span class="report-risk-heading">
              <span class="level-badge ${levelClass(risk.level)}">${risk.level}</span>
              <strong>${risk.project}</strong>
              <i>—</i>
              <b>${risk.category}</b>
            </span>
            <span class="report-risk-description">${risk.description}</span>
            <span class="report-risk-link">查看完整风险详情</span>
          </button>
        `;
      }).join("")
      : `<p class="report-empty">本封周报未提取到风险。</p>`;
    const actionRows = relatedActions.length
      ? relatedActions.map(function (action) {
        return `
          <tr>
            <td><span class="urgency-badge ${urgencyClass(action.urgency)}">${action.urgency}</span></td>
            <td>${action.task}</td>
            <td>${action.owner}</td>
            <td>${action.type}</td>
          </tr>
        `;
      }).join("")
      : `
        <tr class="risk-detail-empty-row">
          <td colspan="4">本封周报暂无关联行动项</td>
        </tr>
      `;

    openModal(
      "WEEKLY REPORT · 邮箱同步",
      report.sender + " 的周报",
      `
        <div class="report-meta">
          <span><b>发件人</b>${report.sender}</span>
          <span><b>发送日期</b>${report.date}</span>
          <span><b>涉及项目</b>${report.projectNames.length}项</span>
          <span><b>同步状态</b>同步成功</span>
          <span><b>AI分析</b>分析完成</span>
        </div>

        <section class="report-key-points">
          <h3><span class="risk-block-icon risk-description-icon" aria-hidden="true"></span>关键要点</h3>
          <p>${report.points}</p>
        </section>

        <section class="report-detail-section">
          <div class="risk-section-title">
            <span class="risk-section-symbol report-project-symbol" aria-hidden="true"></span>
            <div>
              <h3>涉及项目列表</h3>
              <p>根据标准项目清单匹配，并汇总当前最高风险状态</p>
            </div>
          </div>
          <div class="table-scroll">
            <table class="report-project-table">
              <thead>
                <tr>
                  <th>项目名称</th>
                  <th>当前状态</th>
                </tr>
              </thead>
              <tbody>${projectRows}</tbody>
            </table>
          </div>
        </section>

        <section class="report-detail-section">
          <div class="risk-section-title">
            <span class="risk-section-symbol report-risk-symbol" aria-hidden="true"></span>
            <div>
              <h3>提取的风险（${extractedRisks.length}项）</h3>
              <p>保留AI从本封周报中提取的全部风险描述</p>
            </div>
          </div>
          <div class="report-risk-list">${riskCards}</div>
        </section>

        <section class="report-detail-section">
          <div class="risk-section-title">
            <span class="risk-section-symbol risk-action-symbol" aria-hidden="true"></span>
            <div>
              <h3>关联行动项</h3>
              <p>由本封周报涉及项目的风险建议形成</p>
            </div>
          </div>
          <div class="table-scroll">
            <table class="risk-action-table">
              <thead>
                <tr>
                  <th>紧急度</th>
                  <th>事项</th>
                  <th>负责人</th>
                  <th>类型</th>
                </tr>
              </thead>
              <tbody>${actionRows}</tbody>
            </table>
          </div>
        </section>

        <footer class="risk-detail-footer">
          <span>数据来源：${report.sender}周报邮件 · 风险管理员个人邮箱同步</span>
          <span>项目匹配：已与标准项目清单完成匹配</span>
        </footer>
      `
    );
  }

  function openMetricDetail(type) {
    const details = {
      projects: {
        title: "项目总数详情",
        summary: [["在交付项目", "131"], ["交付部门", "8"], ["本批新增", "0"]],
        rows: [["统计口径", "当前有效Excel导入批次中计入项目总数的在交付项目。"], ["数据批次", "IMP-20260723-01"], ["更新时间", "2026-07-23 09:30"]]
      },
      risks: {
        title: "风险总数详情",
        summary: [["风险总数", "12"], ["涉及项目", "8"], ["本周新增", "6"]],
        rows: [["风险构成", "高风险4项、中风险4项、低风险4项。"], ["数据来源", "周报AI提炼10项，日常上报2项。"], ["统计口径", "仅统计当前状态为有效且未解除的风险。"]]
      },
      highRisks: {
        title: "高风险详情",
        summary: [["高风险", "4"], ["涉及项目", "2"], ["本周新增", "2"]],
        rows: [["重点项目", "锡山智慧城市一期、新吴城运数字底座。"], ["优先事项", "供应商核减决策、终验款报价、质保款回款。"], ["更新时间", "2026-07-23 09:30"]]
      },
      remaining: {
        title: "风险项目待回款详情",
        summary: [["待回款", "5,326.30万"], ["风险项目", "6"], ["金额待补充", "2"]],
        rows: [["统计口径", "仅汇总存在有效风险项目的剩余待回款金额。"], ["空值规则", "Excel金额为空不按0计算。"], ["数据来源", "项目清单Excel与补充回款记录。"]]
      },
      collected: {
        title: "风险项目已回款详情",
        summary: [["已回款", "5,410.23万"], ["风险项目", "6"], ["完成率", "50.4%"]],
        rows: [["统计口径", "仅汇总存在有效风险项目的实际已回款金额。"], ["数据批次", "IMP-20260723-01"], ["更新时间", "2026-07-23 09:30"]]
      }
    };
    const detail = details[type];

    if (!detail) return;

    openModal(
      "METRIC DETAIL",
      detail.title,
      `<div class="detail-summary">${detail.summary.map(function (item) {
        return `<div><span>${item[0]}</span><strong>${item[1]}</strong></div>`;
      }).join("")}</div>${detailRows(detail.rows)}`
    );
  }

  function switchPanel(panelName) {
    document.querySelectorAll(".section-tabs button").forEach(function (tab) {
      const active = tab.dataset.panel === panelName;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });

    document.querySelectorAll(".tab-panel").forEach(function (panel) {
      panel.hidden = panel.id !== "panel-" + panelName;
    });
  }

  function openAgent() {
    lastFocusedElement = document.activeElement;
    elements.agentOverlay.hidden = false;
    document.body.style.overflow = "hidden";
    document.getElementById("closeAgent").focus();
  }

  function closeAgent() {
    elements.agentOverlay.hidden = true;
    document.body.style.overflow = "";
    if (lastFocusedElement) lastFocusedElement.focus();
  }

  function addAgentExchange(question) {
    const response = question.includes("高风险")
      ? "当前共有4项高风险，主要集中在锡山智慧城市一期和新吴城运数字底座。建议优先处理供应商核减决策与质保款回款。"
      : question.includes("待办")
        ? "本周共有8项管理者待办，其中2项紧急、2项高优先级。最紧急的是锡山供应商法务决策和集团沟通结果核实。"
        : question.includes("回款")
          ? "当前风险项目待回款5,326.30万元，已回款5,410.23万元。金额只统计存在有效风险的项目。"
          : "已收到你的问题。原型将根据项目清单、周报和日常上报数据进行分析，并返回可追溯的风险结论。";

    elements.agentMessages.insertAdjacentHTML(
      "beforeend",
      `<div class="user-message"><div><p>${escapeHtml(question)}</p></div></div>
       <div class="agent-message">
         <span class="message-avatar" aria-hidden="true">AI</span>
         <div><p>${response}</p></div>
       </div>`
    );
    elements.agentMessages.scrollTop = elements.agentMessages.scrollHeight;
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function activateRow(event, selector, callback) {
    const row = event.target.closest(selector);
    if (row) callback(row);
  }

  populateSelect(
    elements.categoryFilter,
    Array.from(new Set(riskItems.map(function (item) { return item.category; })))
  );
  populateSelect(
    elements.ownerFilter,
    Array.from(new Set(riskItems.map(function (item) { return item.owner; })))
  );
  populateSelect(
    elements.todoOwnerFilter,
    Array.from(new Set(todoItems.map(function (item) { return item.owner; })))
  );

  [
    elements.levelFilter,
    elements.categoryFilter,
    elements.ownerFilter,
    elements.todoOwnerFilter
  ].forEach(enhanceSelect);

  renderRiskTable();
  renderDepartmentTable();
  renderTodoTable();
  renderPaymentTable();
  renderTimeline();
  renderResolvedTable();
  renderReports();

  document.querySelectorAll(".section-tabs button").forEach(function (tab) {
    tab.addEventListener("click", function () {
      switchPanel(tab.dataset.panel);
    });
  });

  [elements.riskSearch, elements.levelFilter, elements.categoryFilter, elements.ownerFilter].forEach(function (control) {
    control.addEventListener(control.tagName === "INPUT" ? "input" : "change", renderRiskTable);
  });

  document.getElementById("resetFilters").addEventListener("click", function () {
    elements.riskSearch.value = "";
    elements.levelFilter.value = "all";
    elements.categoryFilter.value = "all";
    elements.ownerFilter.value = "all";
    [elements.levelFilter, elements.categoryFilter, elements.ownerFilter].forEach(function (select) {
      select.syncCustomSelect();
    });
    renderRiskTable();
  });

  elements.todoOwnerFilter.addEventListener("change", renderTodoTable);

  document.querySelector(".metric-grid").addEventListener("click", function (event) {
    const card = event.target.closest("[data-detail]");
    if (card) openMetricDetail(card.dataset.detail);
  });

  document.querySelector(".focus-list").addEventListener("click", function (event) {
    const button = event.target.closest("[data-risk-id]");
    if (button) openRiskDetail(button.dataset.riskId);
  });

  elements.riskTable.addEventListener("click", function (event) {
    activateRow(event, "[data-risk-id]", function (row) {
      openRiskDetail(row.dataset.riskId);
    });
  });

  elements.departmentTable.addEventListener("click", function (event) {
    activateRow(event, "[data-department-index]", function (row) {
      const item = departmentItems[Number(row.dataset.departmentIndex)];
      const rate = item.plan > 0 ? item.collected / item.plan * 100 : 0;
      openModal(
        "DEPARTMENT DETAIL",
        item.department,
        `<div class="department-detail">
          <div class="detail-summary">
            <div><span>计划回款</span><strong>${formatMoney(item.plan)}</strong></div>
            <div><span>已回款</span><strong>${formatMoney(item.collected)}</strong></div>
            <div><span>完成率</span><strong>${rate.toFixed(1)}%</strong></div>
          </div>
          ${detailRows([
            ["剩余待回款", formatMoney(item.remaining)],
            ["数据来源", "项目清单Excel · 当前有效批次"],
            ["更新时间", "2026-07-23 09:30"]
          ])}
        </div>`
      );
    });
  });

  elements.todoTable.addEventListener("click", function (event) {
    activateRow(event, "[data-todo-index]", function (row) {
      const item = todoItems[Number(row.dataset.todoIndex)];
      openModal(
        "ACTION DETAIL",
        item.task,
        detailRows([
          ["关联项目", item.project],
          ["紧急度", item.urgency],
          ["负责人", item.owner],
          ["待办类型", item.type],
          ["来源", "关联风险建议自动生成"]
        ])
      );
    });
  });

  elements.paymentTable.addEventListener("click", function (event) {
    activateRow(event, "[data-payment-index]", function (row) {
      const item = paymentItems[Number(row.dataset.paymentIndex)];
      const rate = item.plan > 0 ? item.collected / item.plan * 100 : 0;
      openModal(
        "COLLECTION DETAIL",
        item.project,
        `<div class="collection-detail">
          <div class="detail-summary">
            <div><span>计划金额</span><strong>${formatMoney(item.plan)}</strong></div>
            <div><span>已回款</span><strong>${formatMoney(item.collected)}</strong></div>
            <div><span>完成率</span><strong>${rate.toFixed(1)}%</strong></div>
          </div>
          ${detailRows([
            ["待回款", formatMoney(item.remaining)],
            ["下一笔回款", item.next],
            ["统计口径", "仅统计当前存在有效风险的项目。"]
          ])}
        </div>`
      );
    });
  });

  elements.timelineList.addEventListener("click", function (event) {
    activateRow(event, "[data-timeline-index]", function (row) {
      const item = timelineItems[Number(row.dataset.timelineIndex)];
      openModal(
        "TIMELINE DETAIL",
        item.title,
        detailRows([
          ["发生时间", item.date],
          ["风险等级", item.level],
          ["事件状态", item.status],
          ["事件说明", item.description]
        ])
      );
    });
  });

  elements.resolvedTable.addEventListener("click", function (event) {
    activateRow(event, "[data-resolved-index]", function (row) {
      const item = resolvedItems[Number(row.dataset.resolvedIndex)];
      openModal(
        "RESOLVED DETAIL",
        item.project,
        detailRows([
          ["原风险描述", item.description],
          ["原等级", item.level],
          ["原类别", item.category],
          ["解除时间", item.date],
          ["解除原因", item.reason]
        ])
      );
    });
  });

  elements.reportsTable.addEventListener("click", function (event) {
    activateRow(event, "[data-report-index]", function (row) {
      openReportDetail(row.dataset.reportIndex);
    });
  });

  document.querySelectorAll("tbody, .timeline-list").forEach(function (container) {
    container.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        const row = event.target.closest("[tabindex='0']");
        if (row) {
          event.preventDefault();
          row.click();
        }
      }
    });
  });

  document.getElementById("closeModal").addEventListener("click", closeModal);
  elements.modalOverlay.addEventListener("click", function (event) {
    if (event.target === elements.modalOverlay) closeModal();
  });

  elements.modalContent.addEventListener("click", function (event) {
    const relatedRisk = event.target.closest("[data-related-risk-id], [data-report-risk-id]");
    if (relatedRisk) {
      openRiskDetail(
        relatedRisk.dataset.relatedRiskId || relatedRisk.dataset.reportRiskId
      );
    }
  });

  document.getElementById("agentEntry").addEventListener("click", openAgent);
  document.getElementById("closeAgent").addEventListener("click", closeAgent);
  elements.agentOverlay.addEventListener("click", function (event) {
    if (event.target === elements.agentOverlay) closeAgent();
  });

  document.querySelector(".quick-prompts").addEventListener("click", function (event) {
    const button = event.target.closest("button");
    if (button) addAgentExchange(button.textContent.trim());
  });

  document.getElementById("agentForm").addEventListener("submit", function (event) {
    event.preventDefault();
    const input = document.getElementById("agentInput");
    const question = input.value.trim();
    if (!question) {
      input.focus();
      return;
    }
    addAgentExchange(question);
    input.value = "";
  });

  const profileButton = document.getElementById("profileButton");
  const profileMenu = document.getElementById("profileMenu");

  profileButton.addEventListener("click", function () {
    const expanded = profileButton.getAttribute("aria-expanded") === "true";
    profileButton.setAttribute("aria-expanded", String(!expanded));
    profileMenu.hidden = expanded;
  });

  profileMenu.addEventListener("click", function (event) {
    const action = event.target.dataset.profileAction;
    if (!action) return;
    profileMenu.hidden = true;
    profileButton.setAttribute("aria-expanded", "false");
    if (action === "password") {
      showToast("密码修改入口已打开");
    }
  });

  document.addEventListener("click", function (event) {
    if (!event.target.closest(".custom-select")) {
      closeOtherCustomSelects(null);
    }
    if (!profileButton.contains(event.target) && !profileMenu.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
  });

  document.getElementById("noticeButton").addEventListener("click", function () {
    showToast("3条待处理提醒：2项高风险、1项回款节点");
  });

  document.getElementById("refreshButton").addEventListener("click", function (event) {
    const button = event.currentTarget;
    button.classList.add("is-loading");
    button.disabled = true;
    window.setTimeout(function () {
      const now = new Date();
      document.getElementById("updatedAt").textContent =
        now.getFullYear() + "-" +
        String(now.getMonth() + 1).padStart(2, "0") + "-" +
        String(now.getDate()).padStart(2, "0") + " " +
        String(now.getHours()).padStart(2, "0") + ":" +
        String(now.getMinutes()).padStart(2, "0");
      button.classList.remove("is-loading");
      button.disabled = false;
      showToast("看板数据已刷新");
    }, 650);
  });

  document.getElementById("syncReportsButton").addEventListener("click", function (event) {
    const button = event.currentTarget;
    button.classList.add("is-loading");
    button.disabled = true;
    window.setTimeout(function () {
      button.classList.remove("is-loading");
      button.disabled = false;
      showToast("已完成邮箱同步，本周暂无新增周报");
    }, 800);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (!elements.modalOverlay.hidden) {
      closeModal();
    } else if (!elements.agentOverlay.hidden) {
      closeAgent();
    }
  });
}());
