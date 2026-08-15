# Release Notes — INTERNAL_MVP（首版内部发布）

- **Release profile:** `INTERNAL_MVP`（ADR 0033）
- **发布范围:** 低并发、内部使用（single internal-server Docker Compose）
- **发布日期:** 2026-08-15
- **代码基线（HEAD）:** `60f59fb2c88fedaab7fcb014494563ca019796f9`
- **release-policy checkpoint（ADR 0033）:** `5bb724a6cf54208fd9305d241130beebb8c611a1`
- **T047 code checkpoint:** `425c0fde081343f3c74b0c45733deb282a6cdcc6`
- **T038 历史结果:** `REVIEW_FAILED` / `FAIL`（不覆写；disposition = `DEFERRED_FOR_INTERNAL_MVP`）

> 本文件为面向人发布说明，简体中文。状态枚举、路径、命令、标识符保持原样。

## 1. 这是什么

project-risk-system 的首版正式发布，**面向低并发、内部使用**。功能 / 安全 / 授权 /
审计 / 可靠性 / 备份恢复 / 部署 gate 均已通过 Independent Review 与 Integration
（Wave 1–24 `PASS`；T001–T047 `REVIEW_PASSED`）。production runtime 为 Python FastAPI
模块化单体 + Celery + Redis（broker only）+ PostgreSQL 16 + ADR 0030 单活 scheduler，
单机内网 Docker Compose 部署，**无 NestJS / Prisma runtime、无双写**。

## 2. 已通过的 gate（INTERNAL_MVP 必需 gate，与 ADR 0033 §1 一致）

- **功能验收:** 全部 module/review/integration gate 通过（Wave 1–24 `PASS`；
  T001–T047 `REVIEW_PASSED`）。
- **安全 / 授权:** T037 acceptance suite `REVIEW_PASSED`（CSRF origin / SSRF guard /
  Excel 上传安全 / closed Agent Provider fail-closed / 四角色×五数据范围授权矩阵 +
  范围外 404 非 leaky 403 + 归档排除）。
- **审计:** 审计哈希链完整性 + append-only DB trigger 不可变性（真实
  UPDATE/DELETE/TRUNCATE 拒绝）`REVIEW_PASSED`。
- **可靠性:** 幂等 enqueue / crashed-worker 恢复 / orphan gen-0 redispatch /
  outbox→Celery 派发 / 回滚原子性 `PASS`（T037、Wave 16/21/22）。
- **备份 / 恢复:** T036 加密备份/恢复 + 真实 PostgreSQL 16 drill `REVIEW_PASSED`；
  ADR 0009 RPO 24h / RTO 4h 基线在低并发内部使用下不被违反。
- **部署:** T035 单机内网 Docker Compose（7 服务，仅 Python API，无 NestJS/Prisma
  runtime、无双写）`REVIEW_PASSED`。
- **T047 `/api/todos` 分页 remediation:** `REVIEW_PASSED`（SQL-layer `LIMIT`/`OFFSET` +
  独立 count，消除全量物化；code checkpoint `425c0fd`）。

## 3. 已知限制与未解决项（operational limitations）

> 以下各项**未解决**，不得描述为已修复 / 已认证 / 已通过。它们是
> PRODUCTION_CAPACITY_READY milestone 的正式阻断项，本首版以「低并发内部使用」为范围
> 接受其存在。

1. **NOT capacity-certified（未通过容量认证）.** ADR 0032 50-VU capacity 认证未通过、
   未重跑、未声称 certified。T038 历史结果保持 `REVIEW_FAILED` / `FAIL`（两轮均多
   hard-gate FAIL，结构性、确定性，非 flaky；Independent Review `CONFIRMED-FAIL`）。
   本首版**不作为面向公网 / 高并发 / 外部 Provider 的生产发布**。
2. **T038 remains FAIL / deferred.** T038 状态不变 = `REVIEW_FAILED`；
   `DEFERRED_FOR_INTERNAL_MVP` 仅为发布处置 metadata，**非 Task PASS**。原 FAIL load
   evidence 完整保留（未删除）；ADR 0032 数值阈值未修改。
3. **SSE initial-event p95 ≤2s 未认证.** 会话创建不写同步 `AgentEvent`，首个 SSE 帧受
   ADR 0030 outbox-drain cadence（默认 5s）下界约束；T038 substituted-path 测得 ~5–6s
   > 2s gate（跨 ADR 0030 §3 drain 5s vs ADR 0032 §6 ≤2s 张力，结构性）。**未认证。**
4. **SSE heartbeat / transport keepalive 未认证.** `_stream` 无 transport keepalive
   （不发 `: ping`）；ADR 0032 §9 deterministic fake Provider 无 production 注入点
   （`build_provider` 无条件），无可测长流。T038 标 `UNVERIFIED`。**未认证。**
5. **deterministic Provider capacity-test seam 未完成.** ADR 0032 §9 fake Provider 在
   production 无注入点，无法在不修改 production 的前提下接线。**未完成。**
6. **connection-pool 调优 deferred.** 单 uvicorn + SQLAlchemy 默认池（pool_size=5 /
   max_overflow=10）在 50 VU 下饱和；需在 `/api/todos` 分页修复后重新测量再判断。
   **未调优。**
7. **剩余 50-VU API/DB capacity gates deferred.** slow-query / 连接饱和 / lock wait 等
   待 T038 重跑。**未通过。**
8. **`/api/todos` 主因已修但 capacity 未重跑.** T038 主因（`/api/todos` 无分页、全量
   6 表 JOIN 双物化、~7.4MB）已由 T047 remediate，但**这不等于 T038 PASS** —— 50-VU
   capacity 认证仍需在 PRODUCTION_CAPACITY_READY milestone 重跑。

**影响范围说明:** 上述 1–7 项在低并发内部使用下可接受；全量 capacity 认证前必须解决。
T047 分页修复改善了 `/api/todos` 单端点行为，但不改变 T038 整体 `REVIEW_FAILED` 状态。

## 4. 升级到 PRODUCTION_CAPACITY_READY 的路径

INTERNAL_MVP → PRODUCTION_CAPACITY_READY（全量 / 外部发布）须按序：

1. **处理 deferred findings:** 解决第 3 节 1–7 项（SSE 初始事件 / heartbeat、
   Provider test seam、connection-pool 调优、剩余 API/DB gates）——或经正式 ADR 重新
   决策，**不得静默 waive**。
2. **重新执行 T038:** 使用 ADR 0032 reference environment / methodology（T035 单机
   Compose、50 并发认证 VU、≥60s 窗口、≥30s warmup、每 endpoint class ≥500 样本、
   连续 2 次可重复）。
3. **T038 PASS 后:** 全部 required evidence 完整（per-run + release `result.json`、
   raw artifacts、Independent Review），hard gates 按 ADR 0032 §8 连续 2 次 `PASS`。
4. **准备真实 external inputs:** 真实测试邮箱、真实 AI Provider、TLS / 出站策略、
   恢复演练窗口。
5. **执行 T039:** 真实 IMAP/Provider E2E、恢复演练、前端 E2E、Python-only production
   cutover checklist，按 T039 contract 验收。

在此之前不得进行面向公网 / 高并发 / 外部 Provider 的生产发布。详见 ADR 0033 §2。

## 5. 不变项（本发布未改动）

- ADR 0032 §8 release-gate 语义（PASS/WARN/FAIL、禁止 best-effort PASS、
  substituted-path 不得记 PASS）不变。
- T038 `REVIEW_FAILED` 历史结果不变；设计 §11 全量完成标准不变（由
  PRODUCTION_CAPACITY_READY 达成）。
- fail-closed / security / audit / reliability 语义不放宽；无云依赖。
- 本发布**未修改任何 production code**（release closeout 仅核验与文档）。

## 6. 相关文档

- 发布配置定义: `docs/adr/0033-define-internal-mvp-and-capacity-ready-release-profiles.md`
- 部署: `project-risk-system/infra/README.md`、`project-risk-system/infra/docker-compose.yml`
- 备份 / 恢复 runbook: `project-risk-system/infra/backup/README.md`
- 操作员交接: `docs/implementation/release/INTERNAL-MVP-OPERATOR-HANDOFF.md`
- 发布收尾核验: `docs/implementation/reports/RELEASE-INTERNAL-MVP-CLOSEOUT.md`
- T038 evidence / T047 remediation: `docs/implementation/reports/T038.md`、`T047.md`
