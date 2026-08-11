Repository Guidelines

1. 核心架构与权威来源

项目已进入 implementation 阶段。正式架构以已批准 ADR、冻结设计和 CONTEXT.md 为准。

核心约束：

正式后端：Python FastAPI

数据库：PostgreSQL

架构：模块化单体（Modular Monolith）

异步任务：Celery + Redis

Agent 流式交互：SSE

Agent 只能使用批准的受限工具

Agent 发起持久化写操作前必须显式确认

正式部署：单台内网服务器上的 Docker Compose

必须保持现有前端与 /api contract 兼容

NestJS 和 Prisma 仅用于理解历史行为、数据模型和 API contract：

不属于 production runtime

不新增正式业务功能，除非 Task 明确要求

不允许 NestJS / FastAPI 双写

不允许 FastAPI 在 runtime 调用 NestJS

不建立双后端或双 migration 体系

信息优先级：

已批准 ADR

冻结的正式设计

CONTEXT.md

docs/implementation/BASELINE.md

docs/implementation/GLOBAL_CONSTRAINTS.md

assigned Txxx.md

当前 production code / existing behavior

NestJS reference implementation

prototype / artifacts

低优先级来源与高优先级来源冲突时，使用高优先级来源，不得静默融合。

设计缺少必要决策：报告 DESIGN_GAP，停止相关实现

实现必须偏离批准设计：报告 DESIGN_DEVIATION，等待批准

2. 主要目录

project-risk-system/apps/web/src/：Vue 3 / Vite 正式前端

project-risk-system/apps/api-python/：FastAPI 正式后端

project-risk-system/apps/api/src/：历史 NestJS 参考实现

project-risk-system/apps/api/prisma/：历史 Prisma schema / migration / seed

project-risk-system/packages/contracts/src/：现有 TypeScript contracts，作为 /api 兼容依据

project-risk-system/infra/：正式部署配置

project-risk-system/storage/：批准的运行时数据挂载

docs/、specifications、ui-prototype/、generated artifacts/：设计或参考资料，不得直接视为 production code

不要向 Git 提交 secrets、credentials、邮件正文、用户敏感文件、业务导入数据、数据库备份或其他私有运行时数据。

3. FastAPI 实现规范

按业务领域组织模块，模块内尽量聚合：

API / router

schema / DTO

service / application logic

repository / persistence

domain logic

policy / permission

Celery tasks

tests

要求：

保持明确的领域、服务、数据访问、权限和外部集成边界

避免巨大的全局 routers.py、services.py、models.py

避免隐藏循环依赖

领域术语与 CONTEXT.md 保持一致

遵循当前 Ruff、mypy、formatter 和 editor 配置

保持完整类型标注，不使用大量 Any 绕过检查

除非批准设计明确要求，不引入微服务、service mesh、event sourcing、CQRS infrastructure、Kafka、Kubernetes 或多数据库拆分。

4. 数据库、异步任务与 SSE

PostgreSQL

PostgreSQL 是唯一正式数据库

SQLite 仅可用于不会掩盖 PostgreSQL 特性的局部测试或临时工具

涉及 schema、migration、constraint、index、transaction、concurrency 或 locking 时，必须验证 PostgreSQL 行为

不允许双数据库或双写迁移方案，除非有新 ADR 明确批准

Celery + Redis

明显属于后台任务的重型工作不得直接放在 FastAPI request lifecycle 中执行。

Celery task 应明确：

输入边界

幂等性

retry policy

失败处理

序列化限制

不得把 request-scoped state、不可序列化对象或过大对象传入 task。Redis 不得未经批准扩展为业务主数据库。

SSE

实现 SSE 时必须处理 event contract、连接生命周期、错误、取消、heartbeat / timeout 和 partial response；具体 contract 以 Task 和 ADR 为准。

5. /api 兼容、权限与安全

修改 API 时必须检查：

HTTP method、URL path、query 和 request body

response fields、status codes 和 error shape

pagination、时间序列化、nullability、enum values

permission behavior

不能只验证“返回 200”，必须验证前端真实依赖的行为。可使用现有前端 API client、TypeScript contracts、NestJS reference、contract tests、OpenAPI comparison 和 integration tests 作为证据。

不得为方便 Python 实现而随意修改 /api contract。确需修改时，报告 DESIGN_DEVIATION。

必须保留：

permission checks

project-scope checks

sensitive mutation audit

禁止绕过授权、扩大查询范围或跳过写操作审计。

Agent 只能调用明确批准的工具，不得动态获得任意 shell 或数据库写权限。任何可能改变持久业务状态的 Agent 写操作都必须经过显式确认。

新增配置项时同步更新 .env.example 或批准的等价模板。真实 secrets 通过环境变量注入，不得提交或伪造 production credentials。

6. 工具链与验证

Python 工具链以当前 workspace、pyproject.toml、Task 和 CI 为准，通常包括：

uv

Ruff

mypy

pytest

保持 uv.lock 与依赖声明一致，不得绕过 lockfile 或另建包管理方案。

非交互 shell 不得假设 uv 或 python 已在全局 PATH。优先使用 repository 当前 mise 配置中的明确工具版本：

mise exec uv@<configured-version> python@<configured-version> -- <command>

常见验证：

mise exec uv@<configured-version> python@<configured-version> -- uv sync --frozen
mise exec uv@<configured-version> python@<configured-version> -- uv lock --check
mise exec uv@<configured-version> python@<configured-version> -- uv run --frozen ruff check .
mise exec uv@<configured-version> python@<configured-version> -- uv run --frozen mypy .
mise exec uv@<configured-version> python@<configured-version> -- uv run --frozen pytest -ra

版本和最终命令必须取自当前 repository 配置，不得硬编码旧报告中的版本或机器绝对路径。mise 工具缺失属于执行环境问题，不得通过修改架构或依赖配置绕过。

前端命令从 project-risk-system/ 执行，并以当前 package.json 为准。历史 NestJS / Prisma 命令仅在 Task 明确要求检查参考实现或 contract 时使用。

7. 测试要求

测试应覆盖当前 Task 中有业务或架构风险的内容，尤其是：

service / domain policy

validation / permissions

API contract

PostgreSQL behavior

Celery tasks

Agent tool restrictions

write confirmation

regressions

不得通过删除测试、降低断言、大量 skip、绕过 type checking 或禁用 lint rule 来让 validation 通过。

8. Implementation Task 执行边界

所有实现工作由以下文件驱动：

docs/implementation/TASK_GRAPH.md

docs/implementation/tasks/Txxx-*.md

Implementation Agent 一次只能执行一个 assigned Task。开始前必须读取：

AGENTS.md

docs/implementation/BASELINE.md

docs/implementation/GLOBAL_CONSTRAINTS.md

assigned Txxx.md

Task 引用的正式设计章节

Task 引用的 ADR

当前 Task 所需代码

项目事实以 repository 为准，不依赖 conversation memory。

Implementation Agent 不得：

顺带实现其他 Task 或提前开始下一 Wave

进行无关重构

修改其他 Task definition、TASK_GRAPH.md、ADR 或冻结设计

自行改变架构或扩大 scope

自行修改 Task Graph 状态

Task 状态和调度由 Orchestrator 管理。

9. Review、Integration 与报告

Task 完成后必须由独立 Reviewer 根据 Task specification、acceptance criteria、ADR、diff、tests 和 implementation report 进行判断。

只有当前 Wave 所有 Task 均为 REVIEW_PASSED 后才允许 integration。Integration 后执行项目级 validation，并写入 docs/implementation/reports/WAVE-XX.md。完成一个 Wave 后默认停止，除非用户明确批准继续。

机器可读状态必须保持英文，例如：

PLANNED

READY

IN_PROGRESS

IMPLEMENTED

REVIEW_PASSED

REVIEW_FAILED

INTEGRATED

ACCEPTED

BLOCKED

PASS

FAIL

DESIGN_GAP

DESIGN_DEVIATION

implementation、Review、Integration 和面向人的报告默认使用简体中文；shell commands、代码、路径、API、环境变量、标识符、框架名和状态枚举保持原样。

报告按适用情况记录：

Task / Wave ID 和实施结果

实施摘要与修改文件

API contract、database / migration impact

tests、validation commands 和结果

acceptance criteria

Review / Integration result

风险、blocker、DESIGN_GAP、DESIGN_DEVIATION

下一 Wave readiness

最终事实以经过 Review / Integration 后 repository 中的状态和报告为准。
