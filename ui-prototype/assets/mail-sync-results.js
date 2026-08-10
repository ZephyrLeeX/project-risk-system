(function () {
  "use strict";

  const mailRecords = [
    {
      id: "mail-001",
      batch: "SYNC-20260723-0922",
      status: "completed",
      subject: "【项目周报】锡山智慧城市一期 7月第3周",
      sender: "刘峰 <liufeng@example.com>",
      sentAt: "2026-07-23 08:47",
      processedAt: "2026-07-23 09:23",
      projects: ["锡山智慧城市一期"],
      risks: [
        { level: "高风险", category: "供应商风险", confidence: 96, description: "供应商核减谈判陷入僵局，电信、云联、启明、浪潮均不同意任何核减。", evidence: "周报原文：四家供应商均反馈不同意核减，浪潮明确表示不肯谈，诉讼中。", suggestion: "核实顾书记与集团领导沟通结果；如近期数据局无新方案，建议发起投诉，仍无法解决时准备起诉。" },
        { level: "高风险", category: "回款风险", confidence: 94, description: "终验款592.64万元在途，甲方要求一次性汇总最终报价，仅有一次报价机会。", evidence: "周报原文：终验款592.64万在途，供应商核减报价博弈中，甲方要求我方汇总报价。", suggestion: "7月中旬前完成最终报价内部审核，统一商务、法务和项目口径后提交。" },
        { level: "高风险", category: "客户层面风险", confidence: 91, description: "客户要求统一由我方汇总提交报价，涉及法务和经营决策。", evidence: "周报原文：市数据局要求我方作为总集方统一协调并提交最终方案。", suggestion: "由经营负责人牵头召开专项决策会，明确谈判边界与法律预案。" }
      ],
      keyPoints: [
        "供应商核减谈判陷入僵局，四家供应商均不同意核减，其中浪潮处于诉讼状态。",
        "终验款592.64万元仍在途，甲方要求我方一次性汇总提交最终报价。",
        "建议先核实集团沟通结果，再确定投诉或诉讼路径，并于7月中旬完成报价准备。"
      ],
      attachments: [
        { name: "锡山智慧城市一期_第27周周报.docx", type: "DOCX", size: "284 KB", status: "解析完成" },
        { name: "供应商核减报价汇总.xlsx", type: "XLSX", size: "96 KB", status: "解析完成" }
      ],
      result: "提取3项风险",
      resultNote: "3项待风险管理员确认"
    },
    {
      id: "mail-002",
      batch: "SYNC-20260723-0922",
      status: "completed",
      subject: "新吴城运数字底座项目周报（第27周）",
      sender: "付瑞强 <furuiqiang@example.com>",
      sentAt: "2026-07-23 08:35",
      processedAt: "2026-07-23 09:23",
      projects: ["新吴城运数字底座"],
      risks: [
        { level: "高风险", category: "回款风险", confidence: 95, description: "第一年质保款551.67万元已延期，客户财政状况不理想。", evidence: "周报原文：第一年质保款551.67万已延期，新吴财政情况不理想。", suggestion: "按既定回款计划逐周跟踪，形成书面催收记录并准备升级协调。" },
        { level: "中风险", category: "客户层面风险", confidence: 88, description: "区域财政状况不理想，存在长期拖欠风险。", evidence: "周报多次提到财政资金紧张，原回款时间持续后移。", suggestion: "补充财政支付计划和关键审批节点，设置逾期预警。" }
      ],
      keyPoints: ["质保款551.67万元已经延期。", "客户财政状况不理想，回款时间存在继续后移可能。", "需要按周跟踪付款审批节点并保留书面催收材料。"],
      attachments: [{ name: "新吴城运数字底座_项目周报.pdf", type: "PDF", size: "1.2 MB", status: "解析完成" }],
      result: "提取2项风险",
      resultNote: "1项待确认"
    },
    {
      id: "mail-003",
      batch: "SYNC-20260723-0922",
      status: "completed",
      subject: "昆山鹿路通APP本周进展及风险",
      sender: "肖杰 <xiaojie@example.com>",
      sentAt: "2026-07-23 08:12",
      processedAt: "2026-07-23 09:23",
      projects: ["昆山鹿路通APP"],
      risks: [
        { level: "中风险", category: "交付进度风险", confidence: 89, description: "新增功能项目345万元仍处于招标阶段，7月16日开标后的结果尚待确认。", evidence: "周报原文：新增加功能项目345万招标中，7月16日开标。", suggestion: "跟踪开标结果及合同签署计划，评估对现有交付排期的影响。" }
      ],
      keyPoints: ["新增功能项目金额345万元。", "项目仍处于招标阶段，需跟踪7月16日开标结果。", "现有APP版本开发正常推进。"],
      attachments: [],
      result: "提取1项风险",
      resultNote: "已完成分析"
    },
    {
      id: "mail-004",
      batch: "SYNC-20260723-0922",
      status: "completed",
      subject: "荆州公司项目群2026年第27周周报",
      sender: "田雷 <tianlei@example.com>",
      sentAt: "2026-07-22 18:25",
      processedAt: "2026-07-23 09:23",
      projects: ["荆州公司项目群", "数字荆州二期"],
      risks: [
        { level: "低风险", category: "回款风险", confidence: 86, description: "年度计划回款2161.58万元，目前完成率约5.8%。", evidence: "周报回款表显示实际回款126.05万元，剩余待回款2035.53万元。", suggestion: "按月拆分回款目标，优先跟踪大额节点。" }
      ],
      keyPoints: ["年度计划回款2161.58万元。", "当前实际回款126.05万元，完成率5.8%。", "项目群整体交付进展正常。"],
      attachments: [{ name: "荆州项目群周报.xlsx", type: "XLSX", size: "420 KB", status: "解析完成" }],
      result: "提取1项风险",
      resultNote: "已完成分析"
    },
    {
      id: "mail-005",
      batch: "SYNC-20260723-0922",
      status: "completed",
      subject: "无锡市政务外网三期工作周报",
      sender: "赵振兴 <zhaozhenxing@example.com>",
      sentAt: "2026-07-22 17:42",
      processedAt: "2026-07-23 09:23",
      projects: ["无锡市政务外网三期"],
      risks: [],
      keyPoints: ["本周完成网络设备联调。", "客户验收材料已提交。", "暂未识别到新增风险。"],
      attachments: [{ name: "政务外网三期第27周周报.docx", type: "DOCX", size: "188 KB", status: "解析完成" }],
      result: "未发现新增风险",
      resultNote: "邮件分析完成"
    },
    {
      id: "mail-006",
      batch: "SYNC-20260721-1100",
      status: "skipped",
      subject: "Re: 锡山智慧城市一期第26周周报",
      sender: "刘峰 <liufeng@example.com>",
      sentAt: "2026-07-21 10:18",
      processedAt: "2026-07-21 11:01",
      projects: ["锡山智慧城市一期"],
      risks: [],
      keyPoints: ["该邮件Message-ID已在前一同步批次中完成处理。", "系统未重复调用AI，也未重复写入风险线索。"],
      attachments: [],
      result: "重复邮件",
      resultNote: "按Message-ID去重跳过"
    },
    {
      id: "mail-007",
      batch: "SYNC-20260718-0900",
      status: "failed",
      subject: "市数据局综合事务系统项目周报",
      sender: "刘峰 <liufeng@example.com>",
      sentAt: "2026-07-18 08:15",
      processedAt: "2026-07-18 09:02",
      projects: ["市数据局综合事务系统"],
      risks: [],
      keyPoints: ["邮件正文读取成功。", "附件“项目范围变更说明.pdf”解析失败，未进入AI分析。"],
      attachments: [{ name: "项目范围变更说明.pdf", type: "PDF", size: "18.6 MB", status: "解析失败" }],
      result: "附件解析失败",
      resultNote: "等待风险管理员重试",
      failure: "附件大小超过当前解析上限，系统未执行附件中的任何内容，也未推进该邮件的处理游标。请压缩附件或调整授权范围后重试。"
    },
    {
      id: "mail-008",
      batch: "SYNC-20260716-1700",
      status: "analyzing",
      subject: "锡东先导区CIM平台阶段进展",
      sender: "张哲 <zhangzhe@example.com>",
      sentAt: "2026-07-16 16:22",
      processedAt: "处理中",
      projects: ["锡东先导区CIM平台"],
      risks: [],
      keyPoints: ["正文和附件解析完成。", "AI分析任务正在排队，尚未生成正式风险线索。"],
      attachments: [{ name: "CIM平台阶段进展.docx", type: "DOCX", size: "310 KB", status: "解析完成" }],
      result: "AI分析中",
      resultNote: "已进入分析队列"
    }
  ];

  const batches = [
    { id: "SYNC-20260723-0922", trigger: "manual", operator: "刘峰", startedAt: "2026-07-23 09:22", duration: "1分36秒", scanned: 9, added: 5, risks: 12, status: "completed" },
    { id: "SYNC-20260721-1100", trigger: "auto", operator: "系统任务", startedAt: "2026-07-21 11:00", duration: "52秒", scanned: 6, added: 2, risks: 4, status: "completed" },
    { id: "SYNC-20260718-0900", trigger: "auto", operator: "系统任务", startedAt: "2026-07-18 09:00", duration: "1分08秒", scanned: 5, added: 3, risks: 5, status: "partial" },
    { id: "SYNC-20260716-1700", trigger: "manual", operator: "刘峰", startedAt: "2026-07-16 17:00", duration: "处理中", scanned: 3, added: 1, risks: 0, status: "running" }
  ];

  const statusLabels = {
    completed: "分析完成",
    analyzing: "分析中",
    skipped: "已跳过",
    failed: "处理失败"
  };

  const mailTable = document.getElementById("mailResultTable");
  const batchTable = document.getElementById("batchResultTable");
  const emptyState = document.getElementById("mailEmptyState");
  const mailSearch = document.getElementById("mailSearch");
  const statusFilter = document.getElementById("statusFilter");
  const batchFilter = document.getElementById("batchFilter");
  const mailDetailOverlay = document.getElementById("mailDetailOverlay");
  const mailDetailContent = document.getElementById("mailDetailContent");
  const drawerRetryButton = document.getElementById("drawerRetryButton");
  const drawerRiskAction = document.getElementById("drawerRiskAction");
  const toast = document.getElementById("syncPageToast");
  let activeRecordId = null;
  let toastTimer = 0;

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.querySelector("p").textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(function () {
      toast.hidden = true;
    }, 2500);
  }

  function projectCell(record) {
    const first = record.projects[0] || "待匹配";
    const more = record.projects.length > 1 ? `<em class="project-more-badge">另${record.projects.length - 1}个项目</em>` : "";
    return `<div class="mail-project-cell"><strong>${escapeHtml(first)}</strong>${more}<small>匹配置信度 ${record.projects.length ? "96%" : "--"}</small></div>`;
  }

  function resultCell(record) {
    return `<div class="process-result-cell"><strong>${escapeHtml(record.result)}</strong><small>${escapeHtml(record.resultNote)}</small></div>`;
  }

  function renderMailTable() {
    const query = mailSearch.value.trim().toLowerCase();
    const status = statusFilter.value;
    const batch = batchFilter.value;
    const filtered = mailRecords.filter(function (record) {
      const searchable = [record.subject, record.sender, record.projects.join(" ")].join(" ").toLowerCase();
      const matchesQuery = !query || searchable.includes(query);
      const matchesStatus = status === "all" || record.status === status || (status === "risk" && record.risks.length > 0);
      const matchesBatch = batch === "all" || record.batch === batch;
      return matchesQuery && matchesStatus && matchesBatch;
    });

    mailTable.innerHTML = filtered.map(function (record) {
      const action = record.status === "failed"
        ? `<button class="row-retry-button" type="button" data-retry-id="${record.id}">重新处理</button>`
        : `<button class="row-action-button" type="button" aria-label="查看邮件详情"></button>`;
      return `<tr class="${record.status === "failed" ? "is-failed-row" : ""}" data-mail-id="${record.id}" tabindex="0">
        <td><span class="result-status is-${record.status}">${statusLabels[record.status]}</span></td>
        <td><div class="mail-subject-cell"><strong title="${escapeHtml(record.subject)}">${escapeHtml(record.subject)}</strong><small>${escapeHtml(record.sender)}</small></div></td>
        <td><div class="mail-time-cell"><strong>${escapeHtml(record.sentAt.split(" ")[0])}</strong><small>${escapeHtml(record.sentAt.split(" ")[1] || "")}</small></div></td>
        <td>${projectCell(record)}</td>
        <td><span class="risk-count ${record.risks.length ? "" : "is-zero"}"><strong>${record.risks.length}</strong><small>项</small></span></td>
        <td>${resultCell(record)}</td>
        <td>${action}</td>
      </tr>`;
    }).join("");

    emptyState.hidden = filtered.length > 0;
    mailTable.closest(".sync-table-scroll").hidden = filtered.length === 0;
    document.querySelector("#mailPanel .table-footer").hidden = filtered.length === 0;
    document.getElementById("mailResultSummary").textContent =
      `共${filtered.length}封邮件${filtered.some(record => record.status === "failed") ? " · 包含1封历史失败邮件" : ""}`;
    document.getElementById("tableFooterSummary").textContent =
      filtered.length ? `显示 1–${filtered.length}，共${filtered.length}封邮件` : "共0封邮件";
    document.getElementById("mailTabCount").textContent = filtered.length;
  }

  function renderBatchTable() {
    batchTable.innerHTML = batches.map(function (batch) {
      const resultStatus = batch.status === "completed"
        ? '<span class="result-status is-completed">全部完成</span>'
        : batch.status === "partial"
          ? '<span class="result-status is-failed">部分失败</span>'
          : '<span class="result-status is-analyzing">执行中</span>';
      return `<tr data-batch-id="${batch.id}" tabindex="0">
        <td><div class="batch-id-cell"><strong>${batch.id}</strong><small>执行人：${batch.operator}</small></div></td>
        <td><span class="trigger-badge is-${batch.trigger}">${batch.trigger === "manual" ? "手动同步" : "自动同步"}</span></td>
        <td>${batch.startedAt}</td>
        <td>${batch.duration}</td>
        <td><span class="batch-stat"><strong>${batch.scanned} / ${batch.added}</strong><small>封</small></span></td>
        <td><span class="batch-stat"><strong>${batch.risks}</strong><small>项</small></span></td>
        <td>${resultStatus}</td>
        <td><button class="row-action-button" type="button" aria-label="查看此批次邮件"></button></td>
      </tr>`;
    }).join("");
  }

  function riskCards(record) {
    if (!record.risks.length) {
      return '<div class="failure-detail"><strong>未生成风险线索</strong><p>该邮件未识别到新增风险，或当前处理尚未完成。系统不会凭空创建风险数据。</p></div>';
    }
    return `<div class="risk-detail-list">${record.risks.map(function (risk, index) {
      return `<article class="risk-detail-card">
        <div class="risk-card-head">
          <div>
            <span class="risk-level-badge ${risk.level === "高风险" ? "is-high" : "is-medium"}">${risk.level}</span>
            <h4>${escapeHtml(risk.category)} · 线索 #${index + 1}</h4>
          </div>
          <span class="risk-confidence">AI置信度 ${risk.confidence}%</span>
        </div>
        <div class="risk-card-body">
          <div><span>风险描述</span><p>${escapeHtml(risk.description)}</p></div>
          <div><span>原文证据</span><p>${escapeHtml(risk.evidence)}</p></div>
          <div><span>建议措施</span><p>${escapeHtml(risk.suggestion)}</p></div>
          <div><span>处理说明</span><p>该线索尚未直接发布，需风险管理员确认分类、等级和项目关联。</p></div>
        </div>
        <div class="risk-card-actions">
          <button class="risk-ignore-action" type="button" data-risk-action="ignore">忽略线索</button>
          <button class="risk-edit-action" type="button" data-risk-action="edit">调整后确认</button>
          <button class="risk-confirm-action" type="button" data-risk-action="confirm">确认并发布</button>
        </div>
      </article>`;
    }).join("")}</div>`;
  }

  function detailSection(number, title, helper, body) {
    return `<section class="detail-section">
      <header><div><span class="detail-number">${number}</span><h3>${title}</h3></div><span>${helper}</span></header>
      ${body}
    </section>`;
  }

  function openMailDetail(recordId) {
    const record = mailRecords.find(item => item.id === recordId);
    if (!record) return;
    activeRecordId = recordId;
    document.getElementById("mailDetailSubtitle").textContent = record.subject;
    drawerRetryButton.hidden = record.status !== "failed";
    drawerRiskAction.hidden = record.risks.length === 0;

    const summary = `<div class="mail-detail-summary">
      <div><span>处理状态</span><strong>${statusLabels[record.status]}</strong></div>
      <div><span>同步批次</span><strong title="${record.batch}">${record.batch}</strong></div>
      <div><span>发送时间</span><strong>${record.sentAt}</strong></div>
      <div><span>处理时间</span><strong>${record.processedAt}</strong></div>
      <div><span>风险线索</span><strong>${record.risks.length} 项</strong></div>
    </div>`;
    const keyPoints = `<ul class="mail-key-points">${record.keyPoints.map(point => `<li>${escapeHtml(point)}</li>`).join("")}</ul>`;
    const matches = `<div class="match-table">${record.projects.map(project => `<div><strong>${escapeHtml(project)}</strong><span>标准项目清单精确匹配</span><span class="confidence-badge">置信度 96%</span></div>`).join("") || "<div><strong>尚未匹配项目</strong><span>等待人工确认</span><span>--</span></div>"}</div>`;
    const attachments = record.attachments.length
      ? `<div class="attachment-list">${record.attachments.map(file => `<div class="attachment-item"><span class="attachment-icon">${file.type}</span><p><strong>${escapeHtml(file.name)}</strong><small>${file.type} · ${file.size}</small></p><span>${file.status}</span></div>`).join("")}</div>`
      : '<div class="failure-detail"><strong>无附件</strong><p>该邮件仅包含正文，正文已经完成安全清洗和文本提取。</p></div>';
    const traces = `<div class="trace-list">
      <div class="trace-item"><p><strong>邮件读取与去重校验完成</strong><small>Message-ID和UID未发现重复任务</small></p></div>
      <div class="trace-item"><p><strong>正文清洗与附件解析${record.status === "failed" ? "出现异常" : "完成"}</strong><small>移除签名、引用内容及危险标签</small></p></div>
      <div class="trace-item"><p><strong>项目名称匹配${record.projects.length ? "完成" : "待确认"}</strong><small>与项目清单Excel标准名称进行关联</small></p></div>
      <div class="trace-item"><p><strong>AI风险提取${record.status === "completed" ? "完成" : record.status === "failed" ? "未执行" : "处理中"}</strong><small>使用当前启用的AI服务和风险分类规则</small></p></div>
    </div>`;
    const failure = record.failure ? `<div class="failure-detail"><strong>失败原因</strong><p>${escapeHtml(record.failure)}</p></div>` : "";

    mailDetailContent.innerHTML =
      summary +
      (failure ? detailSection("!", "处理异常", "失败任务不会推进UID游标", failure) : "") +
      detailSection("01", "邮件关键要点", "保留原始含义，不显示无关个人信息", keyPoints) +
      detailSection("02", "项目匹配结果", `${record.projects.length}个关联项目`, matches) +
      detailSection("03", "提取的风险线索", `${record.risks.length}项结果`, riskCards(record)) +
      detailSection("04", "邮件附件", `${record.attachments.length}个附件`, attachments) +
      detailSection("05", "处理轨迹", "全流程留痕", traces);

    mailDetailOverlay.hidden = false;
    document.body.style.overflow = "hidden";
    document.getElementById("closeMailDetail").focus();
  }

  function closeMailDetail() {
    mailDetailOverlay.hidden = true;
    document.body.style.overflow = "";
    activeRecordId = null;
  }

  function retryRecord(recordId) {
    const record = mailRecords.find(item => item.id === recordId);
    if (!record) return;
    record.status = "analyzing";
    record.result = "重新处理中";
    record.resultNote = "已进入分析队列";
    renderMailTable();
    if (!mailDetailOverlay.hidden) closeMailDetail();
    showToast("失败邮件已重新进入处理队列");
    window.setTimeout(function () {
      record.status = "completed";
      record.failure = "";
      record.result = "重新处理完成";
      record.resultNote = "附件已完成解析";
      record.keyPoints = ["项目范围变更说明已完成解析。", "客户要求继续履配合审计，工作范围可能超出合同。"];
      record.risks = [
        { level: "中风险", category: "超出合同需求", confidence: 90, description: "客户要求继续配合审计，工作范围可能超出合同约定。", evidence: "附件内容：已完结项目仍要求继续配合审计和资料补充。", suggestion: "核对合同范围，形成书面工作边界并评估追加费用。" }
      ];
      record.attachments[0].status = "解析完成";
      renderMailTable();
      showToast("重新处理完成，提取1项风险线索");
    }, 1900);
  }

  mailTable.addEventListener("click", function (event) {
    const retry = event.target.closest("[data-retry-id]");
    if (retry) {
      event.stopPropagation();
      retryRecord(retry.dataset.retryId);
      return;
    }
    const row = event.target.closest("[data-mail-id]");
    if (row) openMailDetail(row.dataset.mailId);
  });

  mailTable.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest("[data-mail-id]");
    if (row) {
      event.preventDefault();
      openMailDetail(row.dataset.mailId);
    }
  });

  batchTable.addEventListener("click", function (event) {
    const row = event.target.closest("[data-batch-id]");
    if (!row) return;
    document.querySelector("[data-panel='mail']").click();
    setSelectByValue(document.querySelector("[data-filter-select='batch']"), row.dataset.batchId);
    batchFilter.value = row.dataset.batchId;
    renderMailTable();
    document.getElementById("mailPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.querySelectorAll("[data-panel]").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll("[data-panel]").forEach(function (item) {
        const active = item === tab;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", String(active));
      });
      document.getElementById("mailPanel").hidden = tab.dataset.panel !== "mail";
      document.getElementById("batchPanel").hidden = tab.dataset.panel !== "batch";
    });
  });

  function closeSelects(except) {
    document.querySelectorAll(".sync-select").forEach(function (select) {
      if (select === except) return;
      select.querySelector(".sync-select-trigger").setAttribute("aria-expanded", "false");
      select.querySelector(".sync-select-menu").hidden = true;
    });
  }

  function setSelectByValue(select, value) {
    const option = select.querySelector(`[data-value="${value}"]`);
    if (!option) return;
    select.querySelectorAll("[role='option']").forEach(function (item) {
      item.setAttribute("aria-selected", String(item === option));
    });
    select.querySelector(".sync-select-trigger > span").textContent = option.textContent.trim();
    select.querySelector("input[type='hidden']").value = value;
  }

  document.querySelectorAll(".sync-select").forEach(function (select) {
    const trigger = select.querySelector(".sync-select-trigger");
    const menu = select.querySelector(".sync-select-menu");
    trigger.addEventListener("click", function () {
      const expanded = trigger.getAttribute("aria-expanded") === "true";
      closeSelects(select);
      trigger.setAttribute("aria-expanded", String(!expanded));
      menu.hidden = expanded;
    });
    menu.addEventListener("click", function (event) {
      const option = event.target.closest("[role='option']");
      if (!option) return;
      setSelectByValue(select, option.dataset.value);
      trigger.setAttribute("aria-expanded", "false");
      menu.hidden = true;
      renderMailTable();
    });
  });

  mailSearch.addEventListener("input", renderMailTable);

  function resetFilters() {
    mailSearch.value = "";
    setSelectByValue(document.querySelector("[data-filter-select='status']"), "all");
    setSelectByValue(document.querySelector("[data-filter-select='batch']"), "all");
    renderMailTable();
  }

  document.getElementById("resetMailFilters").addEventListener("click", resetFilters);
  document.querySelector("[data-reset-empty]").addEventListener("click", resetFilters);

  document.getElementById("showPendingButton").addEventListener("click", function () {
    setSelectByValue(document.querySelector("[data-filter-select='status']"), "failed");
    setSelectByValue(document.querySelector("[data-filter-select='batch']"), "all");
    renderMailTable();
  });

  document.querySelectorAll("[data-metric-filter]").forEach(function (card) {
    card.addEventListener("click", function () {
      document.querySelector("[data-panel='mail']").click();
      const filter = card.dataset.metricFilter;
      setSelectByValue(document.querySelector("[data-filter-select='status']"), filter);
      setSelectByValue(
        document.querySelector("[data-filter-select='batch']"),
        filter === "failed" || filter === "skipped" ? "all" : "SYNC-20260723-0922"
      );
      renderMailTable();
      document.getElementById("mailPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  document.getElementById("closeMailDetail").addEventListener("click", closeMailDetail);
  document.querySelector("[data-close-drawer]").addEventListener("click", closeMailDetail);
  mailDetailOverlay.addEventListener("click", function (event) {
    if (event.target === mailDetailOverlay) closeMailDetail();
  });

  drawerRetryButton.addEventListener("click", function () {
    if (activeRecordId) retryRecord(activeRecordId);
  });

  drawerRiskAction.addEventListener("click", function () {
    showToast("已定位到风险看板中的关联风险");
  });

  mailDetailContent.addEventListener("click", function (event) {
    const action = event.target.dataset.riskAction;
    if (!action) return;
    const labels = { ignore: "风险线索已忽略", edit: "已打开风险调整入口", confirm: "风险线索已确认并发布到看板" };
    showToast(labels[action]);
  });

  document.getElementById("syncNowButton").addEventListener("click", function (event) {
    const button = event.currentTarget;
    const dialog = document.getElementById("syncProgressDialog");
    const label = document.getElementById("syncProgressLabel");
    const bar = document.getElementById("syncProgressBar");
    const percent = document.getElementById("syncProgressPercent");
    const stages = [
      ["正在检查新增邮件…", "25%", "25%"],
      ["正在解析正文与附件…", "52%", "52%"],
      ["正在匹配标准项目…", "72%", "72%"],
      ["正在提取风险线索…", "91%", "91%"],
      ["正在写入同步结果…", "98%", "98%"]
    ];
    let stage = 0;
    button.classList.add("is-loading");
    button.disabled = true;
    dialog.hidden = false;
    label.textContent = stages[0][0];
    bar.style.width = stages[0][1];
    percent.textContent = stages[0][2];

    const interval = window.setInterval(function () {
      stage += 1;
      if (stage < stages.length) {
        label.textContent = stages[stage][0];
        bar.style.width = stages[stage][1];
        percent.textContent = stages[stage][2];
      }
    }, 420);

    window.setTimeout(function () {
      window.clearInterval(interval);
      dialog.hidden = true;
      button.classList.remove("is-loading");
      button.disabled = false;
      showToast("同步完成：本次未发现新增周报邮件");
    }, 2250);
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
    if (!event.target.closest(".sync-select")) closeSelects(null);
    if (!profileButton.contains(event.target) && !profileMenu.contains(event.target)) {
      profileMenu.hidden = true;
      profileButton.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    closeSelects(null);
    if (!mailDetailOverlay.hidden) closeMailDetail();
  });

  renderMailTable();
  renderBatchTable();
}());
