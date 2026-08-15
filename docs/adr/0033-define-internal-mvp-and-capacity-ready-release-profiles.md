# 0033 — 定义 INTERNAL_MVP 与 PRODUCTION_CAPACITY_READY 发布配置（release profiles）

## Status

Accepted — 定义两个发布配置及其 capacity-track 延后（deferred）处置语义。

本 ADR 不修改任何 production code、不修改 ADR 0032 数值阈值、不将 T038 改为 PASS、不删除任何 load
evidence。它仅正式化一个**发布处置（release disposition）**：在功能/安全/授权/审计/可靠性/备份恢复
gate 已全部通过的前提下，允许首版以「低并发内部使用」为范围继续发布流程，同时将全量 capacity 认证
与外部 E2E 推迟至独立的 PRODUCTION_CAPACITY_READY milestone。

## Context

设计 §11（完成标准）是**全量发布**基准，其中包含两项尚未满足的条目：

- 「在容量基线数据集上通过查询和任务吞吐验收」—— 由 T038（ADR 0032）门控；
- 「使用真实测试邮箱和真实 AI Provider 完成端到端验收」+「从备份实际恢复……并验证审计链」——
  由 T039（外部 E2E + cutover）门控。

当前实施状态（截至 HEAD `a81fd65`）：

- **全部功能 Task T001–T047 均 `REVIEW_PASSED`**；Wave 1–24 Integration 均 `PASS`；T047 `/api/todos`
  分页 remediation `REVIEW_PASSED`（code checkpoint `425c0fd`）。
- 功能/安全/授权/审计/可靠性/备份恢复 gate 均已通过 Independent Review 与 Integration：
  - 安全/授权/审计/可靠性：T037 acceptance suite（CSRF / SSRF guard / Excel 上传安全 / closed Agent
    Provider fail-closed / 四角色×五数据范围授权矩阵 / 审计哈希链完整性 + append-only DB trigger /
    幂等 enqueue / crashed-worker 恢复 / orphan redispatch / outbox→Celery 派发 / 回滚原子性）`REVIEW_PASSED`。
  - 备份/恢复：T036 加密备份/恢复 + 真实 PostgreSQL 16 drill `REVIEW_PASSED`；ADR 0009 RPO 24h / RTO 4h
    基线未被低并发内部使用违反。
  - 部署拓扑：T035 单机内网 Docker Compose（7 服务，无 NestJS）`REVIEW_PASSED`。
- **T038 capacity/resilience acceptance 在 Wave 25 执行 = `REVIEW_FAILED` / release_verdict `FAIL`**
  （两轮均多 hard-gate FAIL，结构性、确定性，非 flaky；Independent Review `CONFIRMED-FAIL`）。
  - 主因（`/api/todos` 无分页、全量 6 表 JOIN 双重物化）已由 **T047** remediate（`REVIEW_PASSED`）。
  - **T038 状态不变 = `REVIEW_FAILED`**；50-VU ADR 0032 capacity 认证未重跑、未通过、未声称 certified。

T038 识别但 T047 未修的剩余 findings（均经源码核实为真实 production 容量/韧性缺陷）：

- full 50-VU ADR 0032 capacity 认证（T038 重跑）；
- SSE 初始事件 p95 ≤ 2s（跨 ADR 0030 §3 drain 5s vs ADR 0032 §6 ≤2s 张力，会话创建不写同步 `AgentEvent`）；
- SSE heartbeat / transport keepalive 验证（`_stream` 无 keepalive；fast-fail 流 ~5s 关闭无可测长流）；
- deterministic Provider test seam（ADR 0032 §9 fake Provider 无 production 注入点，`build_provider` 无条件）；
- connection-pool 调优（单 uvicorn + SQLAlchemy 默认池在 50 VU 下饱和；需在分页修复后重新测量再判断）；
- 剩余 API/DB capacity gates（slow-query / 连接饱和 / lock wait，待 T038 重跑）。

产品目标：**先交付低并发、内部使用的首版**。全量 capacity 认证与真实外部 E2E 不是该首版的必需项。
然而设计 §11 是单一全量基准，未定义「部分发布」的合法路径。本 ADR 补充该路径，使首版可在不伪造
capacity 认证、不降低既有安全/审计/可靠性 gate 的前提下继续发布流程。

## Decision

定义两个发布配置（release profiles）。二者皆为正式发布状态，而非 Task 状态；不覆写任何 Task 的
`REVIEW_PASSED`/`REVIEW_FAILED`/`PASS`/`FAIL` 历史。

### 1. INTERNAL_MVP（首版内部发布）

允许首版继续发布流程，但必须满足：

#### 必需 gate（已通过的既有 gate，不得降低）

- **功能验收**：全部 module/review/integration gate 已通过（Wave 1–24 `PASS`；T001–T047 `REVIEW_PASSED`）。
- **安全 / 授权**：T037 acceptance suite（CSRF origin / SSRF guard / Excel 上传安全 / closed Agent
  Provider fail-closed / 四角色×五数据范围授权矩阵 + 范围外 404 非 leaky 403 + 归档排除）`REVIEW_PASSED`。
- **审计**：审计哈希链完整性 + append-only DB trigger 不可变性（真实 UPDATE/DELETE/TRUNCATE 拒绝）`REVIEW_PASSED`。
- **可靠性**：幂等 enqueue / crashed-worker 恢复 / orphan gen-0 redispatch / outbox→Celery 派发 /
  回滚原子性（T037、Wave 16/21/22）`PASS`。
- **备份 / 恢复**：T036 加密备份/恢复 + 真实 PostgreSQL 16 drill `REVIEW_PASSED`；ADR 0009 RPO 24h /
  RTO 4h 基线在低并发内部使用下不被违反。
- **部署**：T035 单机内网 Docker Compose（仅 Python API，无 NestJS/Prisma runtime、无双写）`REVIEW_PASSED`。
- **T047 `/api/todos` 分页 remediation 必须保持 `REVIEW_PASSED`**（code checkpoint `425c0fd`）。

#### 真实性约束

- **T038 capacity 结果保持真实记录为 `REVIEW_FAILED` / `FAIL`**——不覆写、不 waive、不改为 PASS。
- **不得声称 capacity-certified**；任何对外材料不得暗示已通过 ADR 0032 50-VU capacity 认证。
- **面向低并发、内部使用**；不作为面向公网/高并发/外部 Provider 的生产发布。
- **已知性能限制必须进入 release notes / operational limitations**（见下「deferred findings」清单）。

#### Deferred findings（不作为 INTERNAL_MVP blocker）

以下 findings **不作为 INTERNAL_MVP 阻断项**，但**仍作为 PRODUCTION_CAPACITY_READY milestone 的正式
阻断项**——不删除、不 waive、不改成 PASS：

- full ADR 0032 50-VU capacity 认证（T038 重跑）；
- SSE 初始事件 p95 ≤ 2s；
- SSE heartbeat / transport keepalive 验证；
- deterministic Provider test seam（ADR 0032 §9）；
- connection-pool 调优（需在分页修复后重新测量再判断）；
- 剩余 API/DB capacity gates（slow-query / 连接饱和 / lock wait）。

这些 findings 须在 INTERNAL_MVP 的 release notes / operational limitations 中如实列出，并标注其影响
范围（低并发内部使用下可接受、全量 capacity 认证前必须解决）。

### 2. PRODUCTION_CAPACITY_READY（全量 / 外部发布）

必须满足：

- **重新执行 T038**，使用 ADR 0032 reference environment / methodology（T035 单机 Compose、50 并发认证
  VU、≥60s 窗口、≥30s warmup、每 endpoint class ≥500 样本、连续 2 次可重复）。
- **全部 required evidence 完整**（per-run + release `result.json`、raw artifacts、Independent Review）。
- **hard gates 按 ADR 0032 §8 `PASS`**（连续 2 次满足硬阻断、证据完整、可重复到可接受方差）。
- **deferred findings 在此 milestone 前完成或经正式 ADR 重新决策**（不得静默 waive）。
- 随后执行 **T039**（真实 IMAP/Provider E2E、恢复演练、前端 E2E、Python-only production cutover
  checklist），按 T039 contract 验收。

PRODUCTION_CAPACITY_READY 是设计 §11 全量完成标准的正式达成点；在此之前不得进行面向公网/高并发/
外部 Provider 的生产发布。

### 3. Status / orchestration semantics

- **T038 历史执行结果仍为 `REVIEW_FAILED` / `FAIL`**——不覆写历史结果。
- 新增 `DEFERRED_FOR_INTERNAL_MVP` 作为**发布处置（release disposition）metadata**，而非伪造的 Task
  PASS。它表示「T038 的 capacity 结果对 INTERNAL_MVP 不阻断，但对 PRODUCTION_CAPACITY_READY 仍阻断」。
  任何 Task report / EXECUTION_STATE 中 T038 的状态字段保持 `REVIEW_FAILED`。
- **T047 记录为已完成 remediation**（`REVIEW_PASSED`，已在其 report 记录 deferred findings）。
- **Wave 25 不执行 Integration**——因 T038 本身未 `PASS`，Wave 25 Integration 不满足「全部必要 Review
  通过」前提。Wave 25 记录为 **capacity-track deferred / closed-for-MVP**，**不是** Integration `PASS`。
- **INTERNAL_MVP 后续 Task**：在 MVP 范围内**无必需后续 Task**——全部功能 Task 已 `REVIEW_PASSED`。
  延后的 capacity/external 工作受 PRODUCTION_CAPACITY_READY 授权门控，不在 INTERNAL_MVP 内启动。
- **T039 readiness 判断（依据 T039 Task contract，非猜测）**：
  - T039 contract：Objective =「Demonstrate real mailbox-to-risk and Agent flows, restore readiness,
    frontend E2E and Python-only production release」；Dependencies =「T038; all design gaps resolved;
    external materials supplied」；Stop conditions =「Missing real inputs or any prerequisite FAIL」；
    Acceptance =「All design §11 completion items and approved thresholds PASS」。
  - 据此，**T039 属 capacity / 外部认证 track，不是 INTERNAL_MVP 必需项**：它显式依赖 T038 `PASS` +
    外部材料（真实测试邮箱、真实 Provider、TLS/出站策略、恢复演练窗口），其 stop condition 明确
    「any prerequisite FAIL」即停。
  - 故 **T039 保持 `DEFERRED`**（`BLOCKED_EXTERNAL_INPUTS`，blocked on T038 `PASS` + external materials），
    不属于 INTERNAL_MVP，不在此 milestone 启动。T039 在 PRODUCTION_CAPACITY_READY milestone（T038 重跑
    PASS 后）方可执行。

## Consequences

- 正向：INTERNAL_MVP 可在功能/安全/授权/审计/可靠性/备份恢复 gate 全部通过且 T047 分页 remediation
  `REVIEW_PASSED` 的前提下，以低并发内部使用为范围继续发布流程，无需伪造 capacity 认证。
- 正向：deferred capacity findings 作为 PRODUCTION_CAPACITY_READY 的正式阻断项保留，不被静默消除；
  首版 release notes / operational limitations 必须如实披露。
- 约束：本 ADR **不修改 ADR 0032 任何数值阈值**；**不将 T038 改为 PASS**；**不删除任何 load evidence**；
  **不修改任何 production code**；不处理 SSE / connection-pool / 其他性能优化（均属
  PRODUCTION_CAPACITY_READY milestone 范围，需显式授权）。
- 约束：INTERNAL_MVP 不得作为面向公网/高并发/外部 Provider 的生产发布；任何对外材料不得声称
  capacity-certified。
- 不变：ADR 0032 §8 release-gate 语义（PASS/WARN/FAIL、禁止 best-effort PASS、substituted-path 不得
  记 PASS）不变；T038 `REVIEW_FAILED` 历史结果不变；设计 §11 全量完成标准不变（由
  PRODUCTION_CAPACITY_READY 达成）。
- 不变：fail-closed / security / audit / reliability 语义不放宽；无云依赖；无 production code 变更。
