#!/usr/bin/env python3
"""Generate synthetic INTERNAL_MVP demo mail fixtures (no SMTP, no send).

Produces markdown (human copy-paste send) + RFC 5322 .eml + manifest.json +
README.md + 4 binary attachment fixtures (.txt/.pdf/.docx/.xlsx), under
<root>/artifacts/demo-mails/ (gitignored). Every subject is ``[WSLDEMO]``
prefixed and every body carries a synthetic-data banner. Project names align
to the canonical demo-seed project list below so mailbox project matching has
real, existing targets to resolve against.

Pure standard library only — no reportlab/openpyxl/python-docx. PDF/DOCX/XLSX
are hand-assembled into valid containers and validated against
:func:`risk_platform.mailbox.parsing.parse_attachment` (the production code
path) when the api-python venv is available.

Usage:
    python3 generate_demo_mails.py generate <root>
    python3 generate_demo_mails.py validate <root>
"""

from __future__ import annotations

import io
import json
import os
import sys
import xml.sax.saxutils as su
import zipfile
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Canonical demo-seed project list.
#
# These names MUST stay in lockstep with the demo business-data seed that
# creates the same projects + aliases. Mails reuse them so mailbox project
# matching resolves against real, existing projects.
# ---------------------------------------------------------------------------
PROJECTS: Final[list[str]] = [
    "WSLDEMO-ERP 系统升级",
    "WSLDEMO-供应链平台上线",
    "WSLDEMO-客户数据迁移",
    "WSLDEMO-移动端重构",
    "WSLDEMO-财务系统集成",
    "WSLDEMO-AI 风险识别试点",
    "WSLDEMO-海外交付项目",
    "WSLDEMO-内部安全整改",
]

# Placeholder addresses used only as human test hints in the .md files. They
# are deliberately the example.invalid domain so they cannot be real mailboxes.
# Never used as SMTP credentials — the user copies Subject/Body by hand.
EXAMPLE_SENDER: Final[str] = "wsl-pm@example.invalid"
EXAMPLE_TO: Final[str] = "wsl-risk@example.invalid"

DEMO_BATCH: Final[str] = "WSLDEMO"
BANNER: Final[str] = (
    "【测试邮件｜完全合成数据】\n"
    "本邮件用于 Project Risk System INTERNAL_MVP 功能验证，不包含真实业务信息。"
)

MAIL_DIR: Final[str] = "artifacts/demo-mails"
ATTACHMENT_DIR: Final[str] = "attachments"

# ---------------------------------------------------------------------------
# Scenario matrix. ``expected`` uses only the high-level labels required by
# the spec — never raw Provider output text. Each entry renders to one .md
# + one .eml, and contributes one row to manifest.json.
# ---------------------------------------------------------------------------
SCENARIOS: Final[list[dict[str, str | None]]] = [
    # --- A. 明确高风险 (1-10) ---
    {
        "id": "MAIL-001", "file": "01-project-delay.md",
        "subject": "[WSLDEMO][风险] ERP 数据迁移预计延期两周",
        "scenario": "project_delay", "project": "WSLDEMO-ERP 系统升级",
        "expected": "risk_candidate", "risk_theme": "schedule", "attachment": None,
        "body": (
            "ERP 数据迁移原计划 8 月 30 日完成，当前预计延期至 9 月 13 日，"
            "约延期两周。原因：迁移脚本的表结构映射存在大量差异，需逐表重写映射规则。"
            "影响：UAT 联调时间被压缩，下游财务模块切换依赖的数据准备可能推迟。"
            "建议措施：本周内冻结迁移范围，增派一名 DBA 专项处理表结构差异，"
            "并向业务方正式报备可能延期窗口。"
        ),
    },
    {
        "id": "MAIL-002", "file": "02-vendor-delay.md",
        "subject": "[WSLDEMO][风险] 供应链平台：核心供应商接口晚交付 10 天",
        "scenario": "vendor_delay", "project": "WSLDEMO-供应链平台上线",
        "expected": "risk_candidate", "risk_theme": "supplier", "attachment": None,
        "body": (
            "供应链平台核心供应商接口原定 9 月 5 日交付联调包，现已书面确认延期至 9 月 15 日，"
            "晚 10 天。原因：供应商侧订单服务重构未完成。影响：我们侧的订单-库存联调链路"
            "无法按计划启动，整体上线里程碑存在顺延风险。建议：立即启动第二供应商兜底评估，"
            "并要求对方项目经理给出逐日进度承诺。"
        ),
    },
    {
        "id": "MAIL-003", "file": "03-budget-overrun.md",
        "subject": "[WSLDEMO][风险] 财务系统集成预算超支 20%",
        "scenario": "budget_overrun", "project": "WSLDEMO-财务系统集成",
        "expected": "risk_candidate", "risk_theme": "cost", "attachment": None,
        "body": (
            "财务系统集成原预算 80 万，当前预测成本 96 万，超支 16 万、约 20%。"
            "原因是第三方实施成本增加（接口定制与对账规则改造远超初评），"
            "另含一笔未预估的数据清洗人工费。影响：年度 IT 预算需追加或调减其他项目。"
            "建议：本周提交预算变更说明，并复核第三方报价中可裁剪范围。"
        ),
    },
    {
        "id": "MAIL-004", "file": "04-customer-acceptance-delay.md",
        "subject": "[WSLDEMO][风险] 客户数据迁移：甲方验收预计延期",
        "scenario": "customer_acceptance_delay", "project": "WSLDEMO-客户数据迁移",
        "expected": "risk_candidate", "risk_theme": "acceptance_delay", "attachment": None,
        "body": (
            "客户数据迁移的甲方验收原定 9 月 20 日，现因甲方数据治理负责人调整，"
            "预计延期至 10 月中旬。甲方尚未给出新的正式验收日期。影响：迁移侧的"
            "上线申请与尾款结算都将顺延。建议：拉通甲方项目接口人本周内确认新验收窗口，"
            "并同步合同方评估延期带来的里程碑条款影响。"
        ),
    },
    {
        "id": "MAIL-005", "file": "05-data-quality-risk.md",
        "subject": "[WSLDEMO][风险] ERP 数据迁移质量：主数据重复率偏高",
        "scenario": "data_quality", "project": "WSLDEMO-ERP 系统升级",
        "expected": "risk_candidate", "risk_theme": "data_quality", "attachment": None,
        "body": (
            "ERP 数据迁移首轮质量校验显示：物料主数据重复率 6.8%、供应商主数据存在 320 条"
            "历史脏数据，远超 2% 的验收阈值。如未在切换前清洗完成，将导致库存与采购模块"
            "上线后数据错乱。影响：可能触发回滚或被迫降级切换。建议：启动专项数据清洗任务，"
            "按主数据类别分批交付，每日复盘清洗通过率。"
        ),
    },
    {
        "id": "MAIL-006", "file": "06-severe-test-defects.md",
        "subject": "[WSLDEMO][风险] 供应链平台 UAT：3 个致命缺陷未关闭",
        "scenario": "severe_test_defects", "project": "WSLDEMO-供应链平台上线",
        "expected": "risk_candidate", "risk_theme": "test_quality", "attachment": None,
        "body": (
            "供应链平台 UAT 当前仍有 3 个 P0 缺陷未关闭：订单批量导入在 2 万行以上"
            "出现内存溢出、库存对账在跨日批处理时金额不平、以及供应商冻结状态"
            "在并发场景下不一致。距上线里程碑仅剩 9 天，修复+回归压力很大。"
            "建议：立即评估是否整体后延里程碑，对三个缺陷各指定责任人并按日跟踪。"
        ),
    },
    {
        "id": "MAIL-007", "file": "07-security-remediation-open.md",
        "subject": "[WSLDEMO][风险] 内部安全整改：2 项高危未在里程碑前闭环",
        "scenario": "security_remediation", "project": "WSLDEMO-内部安全整改",
        "expected": "risk_candidate", "risk_theme": "security", "attachment": None,
        "body": (
            "内部安全整改项目本周里程碑前仍需闭环 2 项高危发现：一是内网文件传输服务"
            "允许匿名访问的缺陷，二是测试环境数据库口令硬编码在配置仓库中。"
            "安全侧已标记为上线阻断项。影响：若未在窗口内闭环，相关模块不得进入生产。"
            "建议：本周内完成匿名访问改造与口令轮换，并补充审计日志回溯。"
        ),
    },
    {
        "id": "MAIL-008", "file": "08-key-person-leaving.md",
        "subject": "[WSLDEMO][风险] 移动端重构：核心开发即将离职",
        "scenario": "key_person_leaving", "project": "WSLDEMO-移动端重构",
        "expected": "risk_candidate", "risk_theme": "resource", "attachment": None,
        "body": (
            "移动端重构核心开发已提交离职申请，预计 9 月 30 日最后工作日。"
            "该同事负责支付链路与原生桥接模块，是团队内唯一熟悉两端协议的人。"
            "影响：支付链路后续维护与联调进度存在单点风险。建议：立即启动知识转移，"
            "本周内完成关键模块文档化，并评估短期补人方案。"
        ),
    },
    {
        "id": "MAIL-009", "file": "09-external-dependency-blocker.md",
        "subject": "[WSLDEMO][风险] 海外交付：外部电子签章系统不可用",
        "scenario": "external_dependency_blocker", "project": "WSLDEMO-海外交付项目",
        "expected": "risk_candidate", "risk_theme": "external_dependency", "attachment": None,
        "body": (
            "海外交付项目的客户合同签署依赖的外部电子签章系统连续 4 天不可用，"
            "对方反馈为底层 PKI 升级导致，尚未给出恢复时间。影响：客户合同无法"
            "在约定窗口内签署，交付启动里程碑存在顺延风险。建议：准备纸质签署"
            "兜底流程，并升级至对方技术接口人确认恢复时间表。"
        ),
    },
    {
        "id": "MAIL-010", "file": "10-scope-creep.md",
        "subject": "[WSLDEMO][风险] AI 风险识别试点：范围明显扩大",
        "scenario": "scope_creep", "project": "WSLDEMO-AI 风险识别试点",
        "expected": "risk_candidate", "risk_theme": "scope", "attachment": None,
        "body": (
            "AI 风险识别试点原范围仅覆盖邮件风险抽取，本周业务方追加：周报风险抽取、"
            "待办自动派发建议与风险去重合并判断。范围显著扩大但人天与预算未追加。"
            "影响：里程碑交付压力与准确率达标风险上升。建议：与业务方明确 MVP 边界，"
            "对追加项进入下一期，或追加资源并同步调整里程碑。"
        ),
    },
    # --- B. 中等风险 / 模糊风险 (11-15) ---
    {
        "id": "MAIL-011", "file": "11-maybe-delay-uncertain.md",
        "subject": "[WSLDEMO][状态] ERP 数据迁移进度可能略受影响，待确认",
        "scenario": "uncertain_delay", "project": "WSLDEMO-ERP 系统升级",
        "expected": "ambiguous_manual_review", "risk_theme": "schedule", "attachment": None,
        "body": (
            "ERP 数据迁移这边进度目前看还在计划内，不过下周供应商侧有一次环境变更，"
            "如果变更影响到我们迁移脚本，进度可能会往后推一点，但具体影响多少现在还说不好，"
            "得等下周环境变更完再看。暂时没有明确延期，只是先把可能的变动同步给各方知悉。"
        ),
    },
    {
        "id": "MAIL-012", "file": "12-cost-may-rise-no-amount.md",
        "subject": "[WSLDEMO][状态] 财务系统集成：成本可能增加，金额待估",
        "scenario": "uncertain_cost", "project": "WSLDEMO-财务系统集成",
        "expected": "ambiguous_manual_review", "risk_theme": "cost", "attachment": None,
        "body": (
            "财务系统集成这边第三方反馈对账规则改造可能比初评复杂，成本有可能增加，"
            "但具体加多少他们还没给到数字，需要等他们细化方案后再报。先同步一下，"
            "等有金额我们再正式走变更流程。"
        ),
    },
    {
        "id": "MAIL-013", "file": "13-tight-but-recoverable.md",
        "subject": "[WSLDEMO][状态] 移动端重构：人力偏紧，项目经理认为可追回",
        "scenario": "tight_but_recoverable", "project": "WSLDEMO-移动端重构",
        "expected": "ambiguous_manual_review", "risk_theme": "resource", "attachment": None,
        "body": (
            "移动端重构本周人力偏紧，有两名同学被临时抽调支援线上问题，导致重构侧进度"
            "略落后于本周计划。不过项目经理评估下周支援结束后可以追回，目前不认为会"
            "影响里程碑，会持续观察，如果下周追不回来再升级处理。"
        ),
    },
    {
        "id": "MAIL-014", "file": "14-requirement-change-not-assessed.md",
        "subject": "[WSLDEMO][状态] 客户数据迁移：客户提出需求变更，影响待评估",
        "scenario": "requirement_change_unassessed", "project": "WSLDEMO-客户数据迁移",
        "expected": "ambiguous_manual_review", "risk_theme": "scope", "attachment": None,
        "body": (
            "客户数据迁移侧客户本周提出希望把客户主数据的合并规则从按编号改为按名称+地址，"
            "目前还没有评估这个变更对迁移脚本和工作量的影响，需要等设计同学评估后再给结论。"
            "暂时先记录变更诉求，不确认是否会影响里程碑。"
        ),
    },
    {
        "id": "MAIL-015", "file": "15-test-behind-but-buffer.md",
        "subject": "[WSLDEMO][状态] 供应链平台：测试进度落后，尚有 buffer",
        "scenario": "test_behind_with_buffer", "project": "WSLDEMO-供应链平台上线",
        "expected": "ambiguous_manual_review", "risk_theme": "test_quality", "attachment": None,
        "body": (
            "供应链平台 UAT 进度比计划落后大约一天半，主要因为缺陷回归耗时偏长。"
            "目前看里程碑前还剩 9 天，缓冲时间尚可覆盖，暂不认为有延期风险，"
            "但需要持续盯回归速率，若后半周仍追不上计划再升级。"
        ),
    },
    # --- C. 非风险 (16-20) ---
    {
        "id": "MAIL-016", "file": "16-weekly-meeting-notice.md",
        "subject": "[WSLDEMO][通知] 本周三项目周会，请准备进度汇报",
        "scenario": "weekly_meeting_notice", "project": "WSLDEMO-ERP 系统升级",
        "expected": "likely_no_risk", "risk_theme": None, "attachment": None,
        "body": (
            "各位负责人好，本周三下午 2:00 召开项目周会，请各模块负责人准备本周进度汇报，"
            "重点说明当前进展、下周计划以及需要协调的事项。会议室 3F-会议室 A，"
            "无法到场的同事请提前同步进度文档。谢谢。"
        ),
    },
    {
        "id": "MAIL-017", "file": "17-milestone-on-track.md",
        "subject": "[WSLDEMO][状态] 客户数据迁移：首期数据校验按计划完成",
        "scenario": "milestone_on_track", "project": "WSLDEMO-客户数据迁移",
        "expected": "likely_no_risk", "risk_theme": None, "attachment": None,
        "body": (
            "同步一下客户数据迁移进展：首期数据校验已按计划于本周五完成，校验通过率 98.6%，"
            "符合验收标准。各模块负责人已确认下周进入第二批迁移。整体进度正常，无异常。"
        ),
    },
    {
        "id": "MAIL-018", "file": "18-thanks-team.md",
        "subject": "[WSLDEMO][通知] 感谢各部门在 ERP 升级联调中的配合",
        "scenario": "thanks_team", "project": "WSLDEMO-ERP 系统升级",
        "expected": "likely_no_risk", "risk_theme": None, "attachment": None,
        "body": (
            "各位同事，ERP 系统升级首轮联调顺利通过，感谢各业务部门与开发团队在联调期间的"
            "高效配合与问题响应。请大家继续保持当前节奏，按计划推进后续阶段。再次感谢大家的支持。"
        ),
    },
    {
        "id": "MAIL-019", "file": "19-fyi-status-sync.md",
        "subject": "[WSLDEMO][FYI] 财务系统集成：本周对接纪要",
        "scenario": "fyi_status_sync", "project": "WSLDEMO-财务系统集成",
        "expected": "likely_no_risk", "risk_theme": None, "attachment": None,
        "body": (
            "FYI，本周财务系统集成对接纪要：已与财务部确认对账规则改造范围，"
            "第三方接口联调环境本周到位，下周开始首轮联调。以上为常规状态同步，"
            "供各位知悉，无需特别跟进。"
        ),
    },
    {
        "id": "MAIL-020", "file": "20-training-notice.md",
        "subject": "[WSLDEMO][通知] 内部安全整改：安全意识培训安排",
        "scenario": "training_notice", "project": "WSLDEMO-内部安全整改",
        "expected": "likely_no_risk", "risk_theme": None, "attachment": None,
        "body": (
            "各位同事，配合内部安全整改项目，本周四下午安排一场全员安全意识培训，"
            "主题为常见安全风险与日常防护。请各部门协调同事参加，培训后将进行一次小测验，"
            "成绩计入本季度安全整改考核。会议室与线上会议链接稍后另行通知。"
        ),
    },
    # --- D. 中文复杂邮件 (21-24) ---
    {
        "id": "MAIL-021", "file": "21-long-mixed-content.md",
        "subject": "[WSLDEMO][周报] ERP 系统升级本周进展与事项",
        "scenario": "long_mixed_content", "project": "WSLDEMO-ERP 系统升级",
        "expected": "risk_candidate", "risk_theme": "schedule", "attachment": None,
        "body": (
            "各位好，本周 ERP 系统升级周报如下。\n\n"
            "一、进展：财务模块接口联调完成 70%，报表模块已完成首轮走查，"
            "权限模块已进入回归阶段。\n\n"
            "二、需要关注：数据迁移侧原定 8 月 30 日完成，当前进度落后，"
            "迁移脚本表结构映射存在大量差异，预计延期至 9 月 13 日，约延期两周。"
            "UAT 联调时间将被压缩，建议本周内冻结迁移范围并增派 DBA。\n\n"
            "三、常规状态：报表模块本周无异常；权限模块回归用例通过率 96%；"
            "开发环境本周稳定运行，未出现中断。\n\n"
            "四、行动项：请数据迁移负责人本周五前提交重新评估后的里程碑计划；"
            "请测试侧下周一前补充 UAT 联调用例的优先级标注。"
        ),
    },
    {
        "id": "MAIL-022", "file": "22-reply-chain-style.md",
        "subject": "[WSLDEMO][回复] Re: 供应链平台供应商接口交付时间",
        "scenario": "reply_chain", "project": "WSLDEMO-供应链平台上线",
        "expected": "risk_candidate", "risk_theme": "supplier", "attachment": None,
        "body": (
            "收到。经与供应商项目经理确认，其订单服务接口原定 9 月 5 日的联调包"
            "将延期至 9 月 15 日交付，晚 10 天。我们侧订单-库存联调链路将被迫顺延，"
            "整体上线里程碑存在延期风险。建议立即启动第二供应商兜底评估。\n\n"
            "-----Original Message-----\n"
            "From: wsl-pm@example.invalid\n"
            "Sent: 本周一\n"
            "To: wsl-risk@example.invalid\n"
            "Subject: 供应链平台供应商接口交付时间确认\n\n"
            "麻烦本周内确认下供应商订单服务接口的联调包交付时间，"
            "我们需要据此排联调计划，谢谢。"
        ),
    },
    {
        "id": "MAIL-023", "file": "23-mixed-cn_en.md",
        "subject": "[WSLDEMO][状态] 海外交付：电子签章对接受阻（中英混合）",
        "scenario": "mixed_cn_en", "project": "WSLDEMO-海外交付项目",
        "expected": "risk_candidate", "risk_theme": "external_dependency", "attachment": None,
        "body": (
            "海外交付项目进展同步：客户合同签署依赖的外部电子签章系统已连续 4 天不可用，"
            "对方反馈为底层 PKI 升级导致。English follow-up from the integration team: "
            "the API integration remains blocked by vendor-side certificate provisioning; "
            "no ETA yet. 我们已准备纸质签署兜底流程，并升级至对方技术接口人确认恢复时间。"
            "客户合同无法在约定窗口内签署，交付启动里程碑存在顺延风险。"
        ),
    },
    {
        "id": "MAIL-024", "file": "24-risk-resolved-update.md",
        "subject": "[WSLDEMO][更新] 数据迁移阻塞问题已解决",
        "scenario": "risk_resolved_update", "project": "WSLDEMO-ERP 系统升级",
        "expected": "resolved_update", "risk_theme": "schedule", "attachment": None,
        "body": (
            "更新：此前 ERP 数据迁移因表结构映射差异导致的进度阻塞问题已解决，"
            "映射规则已全部重写并通过校验，迁移工作按新计划推进，9 月 13 日可完成。"
            "该风险已解除，无需再作为跟踪中风险处理。后续按正常节奏推进。"
        ),
    },
    # --- E. 可选附件内容 (attachment scenarios reuse existing projects) ---
    {
        "id": "MAIL-025", "file": "25-attachment-incident-notes.md",
        "subject": "[WSLDEMO][风险] ERP 上线演练：附件为事件记录",
        "scenario": "attachment_risk", "project": "WSLDEMO-ERP 系统升级",
        "expected": "attachment_risk", "risk_theme": "schedule",
        "attachment": "WSLDEMO-incident-notes.txt",
        "body": (
            "ERP 上线演练中发现一个明确的项目风险，详见附件 txt 中的事件记录。"
            "演练中订单批量导入在 2 万行以上出现内存溢出，距离上线里程碑仅剩 9 天，"
            "建议评估是否整体后延里程碑。"
        ),
    },
    {
        "id": "MAIL-026", "file": "26-attachment-supplier-report.md",
        "subject": "[WSLDEMO][风险] 供应链平台：附件为供应商交付风险报告",
        "scenario": "attachment_risk", "project": "WSLDEMO-供应链平台上线",
        "expected": "attachment_risk", "risk_theme": "supplier",
        "attachment": "WSLDEMO-supplier-risk-report.pdf",
        "body": (
            "供应链平台核心供应商接口交付风险详见附件 PDF 报告，包含供应商交付情况、"
            "预计延期、影响范围与缓解措施。核心结论：接口晚交付 10 天，影响联调与上线。"
        ),
    },
    {
        "id": "MAIL-027", "file": "27-attachment-project-status.md",
        "subject": "[WSLDEMO][风险] 财务系统集成：附件为项目状态报告",
        "scenario": "attachment_risk", "project": "WSLDEMO-财务系统集成",
        "expected": "attachment_risk", "risk_theme": "cost",
        "attachment": "WSLDEMO-project-status.docx",
        "body": (
            "财务系统集成项目状态报告见附件 DOCX，包含项目状态、当前风险与下一步行动。"
            "核心风险为预算超支 20%，需本周提交预算变更说明。"
        ),
    },
    {
        "id": "MAIL-028", "file": "28-attachment-risk-register.md",
        "subject": "[WSLDEMO][风险] AI 风险识别试点：附件为风险登记表",
        "scenario": "attachment_risk", "project": "WSLDEMO-AI 风险识别试点",
        "expected": "attachment_risk", "risk_theme": "scope",
        "attachment": "WSLDEMO-risk-register.xlsx",
        "body": (
            "AI 风险识别试点本周风险登记表见附件 XLSX，包含各项目风险、等级、负责人与状态。"
            "其中范围明显扩大一项为新增，需评估是否进入下一期或追加资源。"
        ),
    },
]


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------
def _md_header(scenario: dict[str, str | None]) -> str:
    lines = [
        f"# {scenario['id']} — {scenario['scenario']}",
        "",
        "> 合成测试邮件（synthetic）。To/From 仅为人工测试提示，不是真实凭据。",
        "> 请手工复制 Subject 与 Body，发送到已配置进系统的测试邮箱。",
        "",
        f"Subject: {scenario['subject']}",
        f"To: {EXAMPLE_TO}",
        f"Suggested From: {EXAMPLE_SENDER}",
        f"Scenario: {scenario['scenario']}",
        f"Expected classification: {scenario['expected']}",
        f"Expected project hint: {scenario['project']}",
    ]
    if scenario.get("risk_theme"):
        lines.append(f"Risk theme: {scenario['risk_theme']}")
    if scenario.get("attachment"):
        lines.append(f"Attachment fixture: attachments/{scenario['attachment']}")
    return "\n".join(lines) + "\n"


def render_markdown(scenario: dict[str, str | None]) -> str:
    body = scenario["body"]
    return _md_header(scenario) + "\n---- BODY ----\n\n" + BANNER + "\n\n" + body + "\n"


# ---------------------------------------------------------------------------
# .eml renderer (RFC 5322, UTF-8 base64 body + optional attachment)
# ---------------------------------------------------------------------------
def _attachment_payload(name: str, root: Path) -> tuple[bytes, str, str] | None:
    """Return (bytes, maintype/subtype, filename) for a fixture, or None."""
    path = root / MAIL_DIR / ATTACHMENT_DIR / name
    if not path.exists():
        return None
    ext = path.suffix.lower()
    mime = {
        ".txt": "text/plain",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")
    return path.read_bytes(), mime, name


def render_eml(scenario: dict[str, str | None], root: Path) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = scenario["subject"]
    msg["From"] = f"WSLDemo PM <{EXAMPLE_SENDER}>"
    msg["To"] = f"WSLDemo Risk <{EXAMPLE_TO}>"
    # Deterministic synthetic Message-ID (no real host). IDs must be globally
    # unique per message so IMAP/scheduler dedupe behaves correctly in tests.
    msg["Message-ID"] = f"<{scenario['id'].lower()}-{_synthetic_id(scenario['id'])}@wsl-demo.invalid>"
    msg["Date"] = formatdate(timeval=None, localtime=True)
    msg["X-WSLDemo-Scenario"] = scenario["scenario"]
    msg["X-WSLDemo-Expected"] = scenario["expected"]
    msg.set_content(BANNER + "\n\n" + scenario["body"], subtype="plain", charset="utf-8")
    if scenario.get("attachment"):
        payload = _attachment_payload(str(scenario["attachment"]), root)
        if payload:
            data, mime, filename = payload
            major, _, minor = mime.partition("/")
            msg.add_attachment(
                data,
                maintype=major or "application",
                subtype=minor or "octet-stream",
                filename=filename,
            )
    return msg.as_bytes()


def _synthetic_id(label: str) -> str:
    """Stable, deterministic synthetic suffix for Message-ID."""
    digest = sorted(label.encode("utf-8"))
    return "".join(format((b * 7 + 3) % 36, "36") for b in digest)


# ---------------------------------------------------------------------------
# Binary attachment fixtures (pure stdlib, valid containers)
# ---------------------------------------------------------------------------
def build_pdf(text: str) -> bytes:
    """CJK-capable PDF: Type0 font, Identity-H encoding, ToUnicode CMap."""
    buf = bytearray()

    def put(b: bytes) -> None:
        buf.extend(b)

    put(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    cids = [cp for cp in (ord(c) for c in text) if cp <= 0xFFFF]
    hex_content = "".join(f"{cp:04X}" for cp in cids).encode("ascii")
    stream = b"BT /F1 10 Tf 72 760 Td 16 TL <" + hex_content + b"> Tj ET"

    # ToUnicode CMap mapping each 2-byte CID to its BMP codepoint.
    cmap: list[bytes] = [
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
        b"/CIDSystemInfo << /Registry (WSLDemo) /Ordering (UCS) /Supplement 0 >> def",
        b"/CMapName /WSLDemo-UCS def",
        b"/CMapType 2 def",
        b"1 begincodespacerange <0000> <FFFF> endcodespacerange",
    ]
    pairs = [b"<%04X> <%04X>" % (cp, cp) for cp in cids]
    for i in range(0, len(pairs), 100):
        seg = pairs[i : i + 100]
        cmap.append(b"%d beginbfchar" % len(seg))
        cmap.extend(seg)
        cmap.append(b"endbfchar")
    cmap.append(b"endcmap")
    to_unicode = b"\n".join(cmap)

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /WSLDemoCJK /Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /WSLDemoCJK /CIDSystemInfo << /Registry (WSLDemo) /Ordering (UCS) /Supplement 0 >> /CIDToGIDMap /Identity /W [] >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(to_unicode), to_unicode),
    ]
    offsets: list[int] = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(buf))
        put(b"%d 0 obj\n%s\nendobj\n" % (i, o))
    xref_pos = len(buf)
    n = len(objs) + 1
    put(b"xref\n0 %d\n" % n)
    put(b"0000000000 65535 f \n")
    for off in offsets:
        put(b"%010d 00000 n \n" % off)
    put(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref_pos))
    return bytes(buf)


def _deterministic_zip(members: list[tuple[str, str]]) -> bytes:
    """Build a reproducible zip: fixed mtime + filename, so bytes are stable.

    ``zipfile.ZipFile.writestr`` with a plain str name stamps the *current*
    time into each entry's date_time, making the output nondeterministic.
    Using an explicit ``ZipInfo`` with a fixed timestamp yields byte-stable
    fixtures (verified by generate->validate across interpreters).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in members:
            info = zipfile.ZipInfo(filename=name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, content)
    return buf.getvalue()


def build_docx(paragraphs: list[str]) -> bytes:
    """Minimal valid .docx (word/document.xml + package rels)."""
    body = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    body += '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    for p in paragraphs:
        body += f'<w:p><w:r><w:t xml:space="preserve">{su.escape(p)}</w:t></w:r></w:p>'
    body += "</w:body></w:document>"

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    word_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )
    return _deterministic_zip(
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", rels),
            ("word/_rels/document.xml.rels", word_rels),
            ("word/document.xml", body),
        ]
    )


def build_xlsx(rows: list[list[str | int]]) -> bytes:
    """Minimal valid .xlsx with sharedStrings + one worksheet."""
    strings: list[str] = []

    def s_idx(v: str) -> int:
        if v not in strings:
            strings.append(v)
        return strings.index(v)

    # sharedStrings must reference all strings; build sheet first to populate.
    sheet_parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n']
    sheet_parts.append(
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    )
    for r, row in enumerate(rows, start=1):
        sheet_parts.append(f'<row r="{r}">')
        for c, v in enumerate(row):
            col = chr(ord("A") + c)
            if isinstance(v, int) and not isinstance(v, bool):
                sheet_parts.append(f'<c r="{col}{r}"><v>{v}</v></c>')
            else:
                sheet_parts.append(f'<c r="{col}{r}" t="s"><v>{s_idx(str(v))}</v></c>')
        sheet_parts.append("</row>")
    sheet_parts.append("</sheetData></worksheet>")
    sheet = "".join(sheet_parts)

    shared = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    shared += (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{sum(len(r) for r in rows)}" uniqueCount="{len(strings)}">'
    )
    for v in strings:
        shared += f'<si><t xml:space="preserve">{su.escape(v)}</t></si>'
    shared += "</sst>"

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="风险登记" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    return _deterministic_zip(
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", rels),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", workbook_rels),
            ("xl/worksheets/sheet1.xml", sheet),
            ("xl/sharedStrings.xml", shared),
        ]
    )


def build_txt(content: str) -> bytes:
    return content.encode("utf-8")


# Fixture content definitions.
ATTACHMENT_FIXTURES: Final[dict[str, tuple[str, bytes]]] = {
    "WSLDEMO-incident-notes.txt": (
        "text/plain",
        build_txt(
            "WSLDEMO 事件记录（合成数据）\n"
            "项目：WSLDEMO-ERP 系统升级\n"
            "事件：上线演练中发现订单批量导入在 2 万行以上出现内存溢出。\n"
            "影响：距上线里程碑仅剩 9 天，修复+回归压力很大。\n"
            "结论：这是一个明确的 project risk，建议评估是否整体后延里程碑。\n"
        ),
    ),
    "WSLDEMO-supplier-risk-report.pdf": (
        "application/pdf",
        build_pdf(
            "WSLDEMO 供应商交付风险报告（合成数据）\n"
            "项目：WSLDEMO-供应链平台上线\n"
            "供应商：示例供应商 A\n"
            "交付项：订单服务接口联调包\n"
            "原定日期：9 月 5 日\n"
            "预计延期：晚 10 天，至 9 月 15 日\n"
            "原因：供应商侧订单服务重构未完成。\n"
            "影响范围：订单-库存联调链路无法按计划启动，整体上线里程碑存在顺延风险。\n"
            "缓解措施：启动第二供应商兜底评估；要求对方项目经理给出逐日进度承诺。\n"
        ),
    ),
    "WSLDEMO-project-status.docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        build_docx(
            [
                "WSLDEMO 项目状态报告（合成数据）",
                "项目：WSLDEMO-财务系统集成",
                "项目状态：进行中，存在预算风险。",
                "当前风险：预算超支 20%（原预算 80 万，预测 96 万）。",
                "原因：第三方实施成本增加，接口定制与对账规则改造远超初评。",
                "下一步行动：本周提交预算变更说明；复核第三方报价可裁剪范围。",
            ]
        ),
    ),
    "WSLDEMO-risk-register.xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        build_xlsx(
            [
                ["项目", "风险", "等级", "负责人", "截止日期", "状态"],
                ["WSLDEMO-ERP 系统升级", "里程碑延期", "HIGH", "张明", "2026-09-01", "OPEN"],
                ["WSLDEMO-供应链平台上线", "供应商接口晚交付", "HIGH", "李华", "2026-09-15", "OPEN"],
                ["WSLDEMO-财务系统集成", "预算超支 20%", "HIGH", "王芳", "2026-08-30", "OPEN"],
                ["WSLDEMO-客户数据迁移", "甲方验收延期", "MEDIUM", "刘洋", "2026-10-15", "OPEN"],
                ["WSLDEMO-移动端重构", "核心开发离职", "MEDIUM", "陈静", "2026-09-30", "OPEN"],
                ["WSLDEMO-AI 风险识别试点", "范围明显扩大", "MEDIUM", "赵磊", "2026-09-10", "OPEN"],
                ["WSLDEMO-海外交付项目", "电子签章不可用", "HIGH", "孙强", "2026-08-25", "OPEN"],
                ["WSLDEMO-内部安全整改", "2 项高危未闭环", "HIGH", "周敏", "2026-08-28", "OPEN"],
            ]
        ),
    ),
}


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------
README_CONTENT: Final[str] = """# WSLDEMO 合成测试邮件（INTERNAL_MVP）

本目录由 `infra/deploy/generate-demo-mails.sh` 生成，已 gitignore，**不提交**。
全部为完全合成数据，不包含真实业务信息、真实邮箱、真实凭据。

## 目录结构

- `*.md`          人工复制发送用邮件（Subject + Body + 元信息）
- `*.eml`         标准 RFC 5322 邮件，可导入邮箱客户端查看原文/转发
- `attachments/`  4 个合法二进制附件 fixture（.txt/.pdf/.docx/.xlsx）
- `manifest.json` 邮件清单（与文件一一对应）
- `README.md`     本文件

## 重要边界

- 本工具**只生成测试邮件内容**，不发送邮件、不实现 SMTP、不需要 SMTP 凭据。
- 不修改 mailbox ingest，不向数据库直接插入邮件。
- 你需要**手工**将邮件内容发送到真实测试邮箱，系统随后通过真实
  `IMAP → scheduler → worker → attachment parsing → Provider` 路径读取。

## 推荐人工测试顺序

### 第一批（先验证基础 AI flow）

先发：

- `MAIL-001`（`01-project-delay.md`）— 明确风险（延期）
- `MAIL-002`（`02-vendor-delay.md`）— 另一类明确风险（供应商）
- `MAIL-016`（`16-weekly-meeting-notice.md`）— 非风险

确认候选识别、分类、项目匹配、确认→Risk 流程正常。

### 第二批（模糊、已解决、长邮件）

再发：

- `MAIL-011` ~ `MAIL-015`（ambiguous / uncertain）
- `MAIL-024`（`24-risk-resolved-update.md`，已解决更新）
- `MAIL-021`（`21-long-mixed-content.md`，长邮件含噪声）

观察 Provider 对不确定性的识别是否合理，以及是否会错误对“已解决”创建新风险。

### 第三批（附件）

最后测试附件：

- `MAIL-025`（.txt）→ `MAIL-026`（.pdf）→ `MAIL-027`（.docx）→ `MAIL-028`（.xlsx）

将 `attachments/` 下对应 fixture 手工添加到邮件后发送。

## 端到端验证路径

```
user manual send
      ↓
real mailbox
      ↓
IMAP
      ↓
scheduler
      ↓
worker
      ↓
parser
      ↓
real Provider
      ↓
candidate / review
      ↓
user confirmation
      ↓
Risk / Todo
```

不要使用任何绕过这条路径的“快速测试”数据库写入。

## 手工发送步骤

1. 运行 `./infra/deploy/generate-demo-mails.sh` 生成本目录。
2. 选择一封 `.md` 邮件。
3. 手工复制其中的 `Subject` 与 `---- BODY ----` 下的正文。
4. 发送到已经配置进系统的测试邮箱。
5. 如测试附件：将 `attachments/` 下对应 fixture 手工添加到邮件。
6. 等待 scheduler/mailbox sync，或通过系统现有方式触发同步。
7. 在 UI 中检查 Mail Sync Summary / Messages / Candidate /
   AI classification / Project mapping / Risk category /
   adjust / ignore / confirm / confirmed Risk / Timeline / Audit。
"""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def build_manifest(root: Path) -> dict[str, object]:
    messages = []
    for s in SCENARIOS:
        entry: dict[str, object] = {
            "id": s["id"],
            "file": s["file"],
            "eml": s["file"].rsplit(".", 1)[0] + ".eml",
            "subject": s["subject"],
            "scenario": s["scenario"],
            "project": s["project"],
            "expected": s["expected"],
            "risk_theme": s.get("risk_theme"),
            "attachment": s.get("attachment"),
        }
        messages.append(entry)
    attachments = [
        {"file": name, "format": Path(name).suffix.lstrip(".").upper(), "mime": mime}
        for name, (mime, _bytes) in ATTACHMENT_FIXTURES.items()
    ]
    attachments.sort(key=lambda a: a["file"])
    return {
        "batch": DEMO_BATCH,
        "synthetic": True,
        "no_real_credentials": True,
        "project_names": PROJECTS,
        "messages": messages,
        "attachments": attachments,
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _project_root() -> Path:
    root = os.environ.get("PROJECT_ROOT")
    if root:
        return Path(root)
    # Walk up from CWD to find the directory containing infra/docker-compose.yml.
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "infra" / "docker-compose.yml").exists():
            return candidate
    return Path.cwd()


def generate(root: Path) -> int:
    out_dir = root / MAIL_DIR
    att_dir = out_dir / ATTACHMENT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    att_dir.mkdir(parents=True, exist_ok=True)

    # Attachment fixtures first (eml embeds them).
    for name, (_mime, data) in ATTACHMENT_FIXTURES.items():
        (att_dir / name).write_bytes(data)

    for s in SCENARIOS:
        (out_dir / str(s["file"])).write_text(render_markdown(s), encoding="utf-8")
        eml_name = str(s["file"]).rsplit(".", 1)[0] + ".eml"
        (out_dir / eml_name).write_bytes(render_eml(s, root))

    (out_dir / "manifest.json").write_text(
        json.dumps(build_manifest(root), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "README.md").write_text(README_CONTENT, encoding="utf-8")
    return 0


def validate(root: Path) -> int:
    out_dir = root / MAIL_DIR
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    if not out_dir.exists():
        return _fail(f"output directory missing: {out_dir}")

    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return _fail(f"manifest missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _fail(f"manifest is not valid JSON: {exc}")

    projects = {p for p in manifest.get("project_names", [])}
    if projects != set(PROJECTS):
        fail("manifest project_names does not match canonical PROJECTS list")

    entries = manifest.get("messages", [])
    if len(entries) != len(SCENARIOS):
        fail(f"manifest message count {len(entries)} != scenario count {len(SCENARIOS)}")

    allowed_expected = {
        "risk_candidate",
        "likely_no_risk",
        "ambiguous_manual_review",
        "resolved_update",
        "project_not_found",
        "attachment_risk",
    }
    seen_files: set[str] = set()
    seen_ids: set[str] = set()

    for s, entry in zip(SCENARIOS, entries, strict=False):
        mid = entry.get("id")
        fname = entry.get("file")
        if not mid or not fname:
            fail(f"manifest entry missing id/file: {entry}")
            continue
        seen_ids.add(mid)
        seen_files.add(fname)
        if mid != s["id"]:
            fail(f"manifest id mismatch: {mid} != {s['id']}")
        if fname != s["file"]:
            fail(f"manifest file mismatch: {fname} != {s['file']}")
        if entry.get("expected") not in allowed_expected:
            fail(f"{mid}: expected '{entry.get('expected')}' not in allowed set")
        if entry.get("expected") != s["expected"]:
            fail(f"{mid}: expected {entry.get('expected')} != scenario {s['expected']}")
        if entry.get("project") != s["project"]:
            fail(f"{mid}: project mismatch")
        subject = str(entry.get("subject", ""))
        if not subject.startswith("[WSLDEMO]"):
            fail(f"{mid}: subject missing [WSLDEMO] prefix: {subject!r}")

        md_path = out_dir / fname
        eml_name = str(fname).rsplit(".", 1)[0] + ".eml"
        eml_path = out_dir / eml_name
        for p, kind in [(md_path, "md"), (eml_path, "eml")]:
            if not p.exists():
                fail(f"{mid}: missing {kind} file {p.name}")

        # Markdown must carry Subject, BODY, banner, project hint.
        md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        if f"Subject: {subject}" not in md_text:
            fail(f"{mid}: markdown missing Subject line")
        if "---- BODY ----" not in md_text:
            fail(f"{mid}: markdown missing BODY marker")
        if "【测试邮件｜完全合成数据】" not in md_text:
            fail(f"{mid}: markdown missing synthetic banner")
        if s["project"] not in md_text:
            fail(f"{mid}: project hint '{s['project']}' not in markdown")

        # Project alignment (unless this is the reserved negative case, which
        # we currently do not ship — reserved hook kept for completeness).
        if s["expected"] != "project_not_found" and s["project"] not in PROJECTS:
            fail(f"{mid}: project '{s['project']}' not in canonical list")

    # Orphan / missing files.
    expected_files = {s["file"] for s in SCENARIOS} | {
        str(s["file"]).rsplit(".", 1)[0] + ".eml" for s in SCENARIOS
    } | {"manifest.json", "README.md"}
    actual_files = {p.name for p in out_dir.iterdir() if p.is_file()}
    orphan = actual_files - expected_files
    if orphan:
        fail(f"unexpected files in output dir: {sorted(orphan)}")

    # Attachment fixtures present and valid format (magic bytes).
    for name, (mime, data) in ATTACHMENT_FIXTURES.items():
        p = out_dir / ATTACHMENT_DIR / name
        if not p.exists():
            fail(f"attachment fixture missing: {name}")
            continue
        actual = p.read_bytes()
        if actual != data:
            fail(f"attachment fixture content changed on disk: {name}")
        ext = Path(name).suffix.lower()
        if ext == ".pdf" and not actual.startswith(b"%PDF-"):
            fail(f"{name}: not a real PDF (bad magic)")
        if ext in {".docx", ".xlsx"} and not actual.startswith(b"PK\x03\x04"):
            fail(f"{name}: not a real {ext} (bad ZIP magic)")
        if ext == ".txt":
            try:
                actual.decode("utf-8")
            except UnicodeDecodeError:
                fail(f"{name}: not valid UTF-8")

    # Deep parse via production parse_attachment if available.
    deep = _try_import_parse()
    if deep is not None:
        parse_attachment, parse_mail = deep
        import tempfile

        for name, (mime, data) in ATTACHMENT_FIXTURES.items():
            with tempfile.TemporaryDirectory() as d:
                result = parse_attachment(name, mime, data, Path(d))
                if result.status != "PARSED":
                    fail(
                        f"{name}: production parse_attachment status "
                        f"{result.status} (code={result.code})"
                    )
                elif not result.text:
                    fail(f"{name}: parsed but no text extracted")
        # parse_mail on one eml (covers RFC 5322 + UTF-8 + attachment).
        sample_eml = out_dir / "01-project-delay.eml"
        if sample_eml.exists():
            parsed = parse_mail(sample_eml.read_bytes(), "<fallback>")
            if not parsed.subject.startswith("[WSLDEMO]"):
                fail(f"parse_mail subject wrong: {parsed.subject!r}")
            if "【测试邮件｜完全合成数据】" not in parsed.text:
                fail("parse_mail did not surface synthetic banner in text")
    else:
        print(
            "[validate] api-python venv unavailable — skipping deep "
            "parse_attachment/parse_mail checks (stdlib checks still ran)."
        )

    if failures:
        for f_msg in failures:
            print(f"  [FAIL] {f_msg}")
        return _fail(f"{len(failures)} validation failure(s)")
    print(f"[validate] OK: {len(SCENARIOS)} mails, "
          f"{len(ATTACHMENT_FIXTURES)} attachment fixtures, manifest consistent.")
    return 0


def _try_import_parse() -> tuple[object, object] | None:
    """Import the production parse_attachment + parse_mail.

    The bash entrypoint prefers the api-python venv interpreter, where these
    modules and their deps (pypdf, defusedxml) are available. When validate
    runs under a bare system python3 without those deps, import fails and we
    return None — callers then skip the deep checks (stdlib checks still run).
    """
    root = _project_root()
    sys.path.insert(0, str(root / "apps" / "api-python" / "src"))
    try:
        from risk_platform.mailbox.parsing import (  # type: ignore[import-not-found]
            parse_attachment,
            parse_mail,
        )
    except (ImportError, OSError):
        return None
    return parse_attachment, parse_mail  # type: ignore[return-value]


def _fail(msg: str) -> int:
    print(f"[FATAL] {msg}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    command = argv[1]
    root = _project_root()
    if command == "generate":
        return generate(root)
    if command == "validate":
        return validate(root)
    return _fail(f"unknown command: {command} (use 'generate' or 'validate')")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
