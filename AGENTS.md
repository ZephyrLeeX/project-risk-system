# Repository Guidelines

## 项目架构基线

本项目已经完成架构设计并进入 implementation 阶段。

正式运行架构以已批准的设计文档、`CONTEXT.md` 和 ADR 为准。

核心架构约束如下：

* 正式环境后端仅运行 **Python FastAPI**
* 数据库使用 **PostgreSQL**
* 必须保持现有前端与 `/api` contract 兼容
* 后端采用 **模块化单体（Modular Monolith）**
* 异步任务使用 **Celery + Redis**
* Agent 流式交互使用 **SSE**
* Agent 只能使用批准的受限工具
* Agent 涉及数据写入时必须经过显式确认
* 正式部署目标为单台内网服务器上的 **Docker Compose**
* 现有 NestJS 后端仅作为开发参照和 contract 参考
* NestJS 不属于正式 production runtime
* 不允许 FastAPI 与 NestJS production 双写
* 不允许为了兼容旧实现而引入长期双后端架构

如果本文件与已批准 ADR 或冻结设计发生冲突：

**以已批准 ADR 和冻结设计为最高优先级。**

不得根据本文件中的旧信息推翻已批准架构。

---

## 项目结构与模块组织

主要应用位于：

`project-risk-system/`

这是一个包含现有前端、旧后端参考实现和新 FastAPI 后端的项目仓库。

### Web 前端

`apps/web/src/`

包含 Vue 3 / Vite 前端，主要包括：

* views
* components
* stores
* API clients
* shared styles

前端是现有正式产品的一部分。

实施 FastAPI 后端时，应尽量保持现有前端行为不变，通过保持 `/api` contract 兼容实现后端替换，而不是无必要地修改前端适配新后端。

---

### Python FastAPI 后端

Python FastAPI 是当前和未来的正式后端实现。

具体目录以已经生成的 implementation task 和当前仓库实际结构为准。

新增正式后端功能必须优先实现到 Python FastAPI 后端。

FastAPI 后端采用模块化单体设计。

领域代码应按业务模块组织，并尽量保持：

* API / router
* schema / DTO
* service / application logic
* repository / persistence
* domain logic
* policy / permission
* task definitions
* tests

在领域模块内部聚合。

避免形成一个巨大的全局：

* `routers.py`
* `services.py`
* `models.py`

文件。

跨模块依赖应保持明确，避免隐藏的循环依赖。

---

### NestJS 参考实现

`apps/api/src/`

包含历史 NestJS 后端实现。

该目录目前属于：

**开发参照 / 行为参照 / contract 参照。**

它不是新的 production backend。

Implementation Agent 可以读取 NestJS 代码，用于：

* 理解现有 `/api` 行为
* 理解业务逻辑
* 理解权限规则
* 理解 DTO / validation
* 理解现有数据模型
* 对照前端依赖的 response structure
* 建立 compatibility tests

但除非 assigned Task 明确要求，否则：

* 不要为 NestJS 新增正式业务功能
* 不要把新功能同时实现到 NestJS 和 FastAPI
* 不要建立 NestJS / FastAPI 双写
* 不要将 NestJS 纳入正式 production deployment
* 不要让 FastAPI 在 runtime 调用 NestJS
* 不要为了保留 NestJS 而改变已经批准的 FastAPI 架构

如果 NestJS 行为与批准的新设计发生冲突：

应根据 Task、ADR 和 compatibility 要求判断。

NestJS 本身不是最高权威来源。

---

### Prisma

`apps/api/prisma/`

属于历史 NestJS 实现相关的 Prisma schema、seed 和 migration。

Prisma 可以作为理解旧数据模型的参考来源，但不是新的 FastAPI production persistence layer 的默认实现。

正式 PostgreSQL schema、migration 和 persistence implementation 应按照 FastAPI implementation plan 和对应 Task 执行。

除非 assigned Task 明确要求维护历史 NestJS 环境，否则不要：

* 为新 FastAPI 功能新增 Prisma schema
* 为新 production schema 创建 Prisma migration
* 建立 Prisma 与 Python ORM 的 production 双 migration 体系

数据库 schema 的权威迁移路径应由批准的 FastAPI 架构和对应 ADR 决定。

---

### Shared Contracts

`packages/contracts/src/`

保存现有共享 TypeScript contracts。

这些 contracts 是保持现有前端 `/api` compatibility 的重要参考。

FastAPI implementation 应根据批准的 API contract：

* 保持 endpoint path
* 保持 request semantics
* 保持 response semantics
* 保持必要字段
* 保持错误语义
* 保持前端实际依赖的 contract

Python 后端无需为了代码复用而直接依赖 TypeScript package。

可以通过：

* contract tests
* OpenAPI comparison
* schema mapping
* integration tests

验证 FastAPI 与现有前端 contract 的兼容性。

不得为了方便 Python implementation 而随意修改已有 `/api` contract。

如果确实必须改变 contract：

应报告：

`DESIGN_DEVIATION`

等待批准。

---

### Infrastructure

`infra/`

保存正式部署和运行相关基础设施配置。

目标 production deployment 为：

**单台内网服务器 + Docker Compose**

正式运行组件最终包括批准架构要求的：

* FastAPI
* PostgreSQL
* Redis
* Celery worker
* 必要的 Celery scheduler / supporting process
* Web frontend
* reverse proxy 或其他批准的入口组件

具体服务组成以 deployment Task 和 ADR 为准。

NestJS 不应被加入新的 production Docker Compose runtime。

---

### Storage

`storage/`

用于批准的运行时数据挂载。

不要将以下敏感内容提交 Git：

* 邮件正文
* 用户上传的敏感文件
* 数据库备份
* 导入业务数据
* credentials
* secrets
* runtime-generated private content

---

### Reference Material

根目录下的：

* `docs/`
* specifications
* `ui-prototype/`
* generated `artifacts/`

主要属于设计、规范、原型和参考资料。

`ui-prototype/` 不属于 production implementation。

不得直接把 prototype 当正式代码使用。

如果 prototype 与 approved design 冲突，以 approved design 为准。

---

## Python 后端开发规范

正式后端代码使用 Python。

具体 Python 版本、依赖管理、lint、type checking 和 testing 工具，以：

* 当前 FastAPI workspace
* `pyproject.toml`
* implementation Task
* CI / validation configuration

为准。

当前 implementation 已建立的 Python 工具链包括：

* `uv`
* Ruff
* mypy
* pytest

不得绕过 lockfile 或随意使用另一套包管理方案替代已经确定的环境。

如果 repository 中存在：

`uv.lock`

应保持 lockfile 与依赖声明一致。

---

## Python Validation

开发过程中应运行与当前 Task 影响范围匹配的 validation。

阶段验收通常包括：

* `uv sync --frozen`
* `uv lock --check`
* Ruff
* mypy
* pytest

具体命令应优先使用：

* 当前 Task 指定命令
* `pyproject.toml`
* repository scripts
* CI configuration

不要仅因为本文件中的示例命令与 repository 演进不一致，就强行使用过时命令。

最终应以当前 repository 中可执行的 validation 为准。

---

## 前端开发命令

前端仍使用现有 pnpm workspace。

需要操作前端时，从：

`project-risk-system/`

执行相应 workspace 命令。

例如：

* `pnpm install`
* `pnpm dev`
* `pnpm dev:web`
* `pnpm test`
* `pnpm check`

具体可用命令应以当前 `package.json` 为准。

不要因为历史 NestJS workspace script 仍然存在，就推断 NestJS 是新的 production backend。

---

## 历史 NestJS / Prisma 命令

仓库中可能仍保留历史命令，例如：

* `pnpm dev:api`
* Prisma generate
* Prisma migrate
* Prisma seed

这些命令属于历史 NestJS implementation 或兼容性检查环境。

只有在 assigned Task 明确需要：

* 检查旧 backend 行为
* 启动 reference implementation
* 比较 API contract
* 获取历史 seed / schema 信息

时才使用。

不得把这些命令作为 FastAPI production implementation 的默认开发流程。

---

## 编码风格与命名规范

遵循当前 repository 中实际生效的 formatter、linter 和 editor 配置。

通用要求：

* UTF-8
* LF endings
* 文件末尾保留 newline
* 不允许 trailing whitespace

### Python

Python 代码：

* 遵循 Ruff 当前配置
* 遵循 mypy 当前配置
* 保持类型标注完整
* 优先明确的数据模型和接口边界
* 避免无必要的动态类型
* 不要通过大量 `Any` 绕过 type checking

领域命名应与 `CONTEXT.md` 中已经确定的 domain language 保持一致。

不要自行创造与现有领域语言冲突的新术语。

### TypeScript / Vue

修改现有 TypeScript / Vue 代码时，保持现有项目风格。

包括：

* Vue components / views 使用 PascalCase
* TypeScript modules 使用现有 kebab-case 约定
* class 使用 PascalCase
* 共享 TypeScript contract 优先复用 `@risk-platform/contracts`

---

## FastAPI 模块边界

FastAPI 采用模块化单体。

Implementation Agent 应优先保持：

* 明确的领域边界
* 明确的 service boundary
* 明确的数据访问边界
* 明确的 permission boundary
* 明确的 external integration boundary

禁止因为“以后可能拆微服务”提前制造复杂的网络边界。

除非 approved design 明确要求，否则不要引入：

* 微服务
* service mesh
* event sourcing
* CQRS infrastructure
* Kafka
* Kubernetes
* 多数据库拆分

Celery + Redis 是当前已批准的异步任务基础设施。

---

## PostgreSQL 与数据库规范

正式数据库使用 PostgreSQL。

不得将 SQLite 作为 production database。

SQLite 可以仅在：

* 某些局部测试
* 临时工具

中使用，前提是不会掩盖 PostgreSQL 特有行为。

如果 Task 涉及：

* schema
* migration
* constraint
* index
* transaction
* concurrency
* locking

应针对 PostgreSQL 行为进行验证。

不得建立：

FastAPI 写 PostgreSQL，同时 NestJS 写另一套数据库

或：

FastAPI / NestJS 双写同一业务数据

这样的迁移模式，除非后续获得新的 ADR 明确批准。

---

## Celery 与 Redis

异步后台任务使用：

* Celery
* Redis

不要在 FastAPI request lifecycle 中直接执行明显属于后台任务的重型工作。

Celery Task 应：

* 输入边界明确
* 保持必要的幂等性
* 明确 retry policy
* 明确失败处理
* 避免传递无法序列化或过大的对象
* 避免直接依赖 request-scoped state

Redis 的具体用途必须符合批准设计。

不要未经设计批准把 Redis 扩展成新的业务主数据库。

---

## Agent 功能规范

项目中的 AI Agent 使用 SSE 提供流式交互。

Agent 必须遵守 approved design 定义的安全边界。

### Tool Access

Agent 只能调用明确批准的受限工具。

不得：

* 动态获得任意 shell access
* 动态获得任意 database write capability
* 根据模型自行扩大工具权限

### 写操作确认

任何由 Agent 发起、可能造成持久业务状态变化的写操作，都必须遵守批准设计中的显式确认机制。

不得因为：

* 用户意图“看起来很明确”
* 模型置信度高
* 操作容易回滚

而绕过确认。

### SSE

Agent 的流式接口使用 SSE。

涉及 SSE implementation 时，应注意：

* event contract
* connection lifecycle
* error handling
* cancellation
* heartbeat / timeout strategy
* partial response behavior

具体 contract 以对应 Task 和 ADR 为准。

---

## 测试规范

Python backend 使用 pytest。

测试应覆盖当前 Task 中具有业务或架构风险的逻辑。

重点包括：

* service logic
* domain policies
* request validation
* permissions
* API contract
* database behavior
* Celery tasks
* Agent tool restrictions
* write confirmation
* regressions

前端及历史 TypeScript workspace 中已有 Vitest 测试的部分继续遵循现有测试规范。

不得为了让 validation 通过而：

* 删除有效测试
* 降低已有断言
* 把失败测试改成无意义测试
* 大量 skip 测试
* 绕过 type checking
* 禁用 lint rule 来隐藏当前 Task 引入的问题

如果现有测试与 approved design 冲突，应报告原因。

---

## `/api` 兼容性规范

保持现有前端与 `/api` contract 兼容是正式 implementation 的核心约束。

涉及 API 修改时必须主动检查：

* HTTP method
* URL path
* query parameters
* request body
* response fields
* status codes
* error shape
* pagination
* date / time serialization
* nullability
* enum values
* permission behavior

不要只验证“接口能返回 200”。

应验证现有前端真实依赖的行为。

NestJS reference backend、TypeScript contracts 和现有前端 API client 都可以作为 compatibility evidence，但最终实现必须符合 approved contract。

---

## Security & Configuration

禁止提交：

* `.env`
* API keys
* credentials
* passwords
* access tokens
* refresh tokens
* certificates private keys
* imported spreadsheets
* mail content
* backups
* production database dumps
* generated private runtime data

新增 configuration key 时，应同步更新：

`.env.example`

或项目批准的等价配置模板。

真实：

* mailbox credentials
* AI Provider credentials
* production domain
* TLS certificates

可以在对应 implementation / deployment 阶段提供。

它们不应阻塞与其无关的前期代码建设。

在真实配置尚未提供时：

* 使用明确的 configuration interface
* 使用 environment variables
* 使用安全 placeholder
* 不要写入虚假 production credentials

---

## 权限与审计

必须保留和实现设计要求的：

* permission checks
* project-scope checks
* sensitive mutation audit

不得为了快速实现：

* 绕过权限
* 临时关闭授权
* 扩大数据查询范围
* 跳过写操作审计

如果历史 NestJS permission behavior 与 approved design 不一致，应根据 Task 和 ADR 处理，而不是盲目复制历史 bug。

---

## Implementation Task 执行规范

所有 implementation 工作通过：

`docs/implementation/TASK_GRAPH.md`

以及：

`docs/implementation/tasks/Txxx-*.md`

进行。

Implementation Agent 一次只能执行一个 assigned Task。

开始工作前必须读取：

1. `AGENTS.md`
2. `docs/implementation/BASELINE.md`
3. `docs/implementation/GLOBAL_CONSTRAINTS.md`
4. assigned `Txxx.md`
5. Task 引用的 authoritative design sections
6. Task 引用的 ADR
7. 当前 Task 所需的相关代码

不得依赖之前 conversation 中的隐含信息。

---

## Source of Truth 优先级

Implementation 阶段的信息优先级如下：

1. 已批准 ADR
2. 冻结的正式设计文档
3. `CONTEXT.md` 中的领域语言和明确业务定义
4. `docs/implementation/BASELINE.md`
5. `docs/implementation/GLOBAL_CONSTRAINTS.md`
6. assigned `Txxx.md`
7. 当前 production code / existing behavior
8. NestJS reference implementation
9. prototype / artifacts

如果低优先级来源与高优先级来源冲突：

使用高优先级来源。

不要静默融合两个互相冲突的设计。

---

## Task Boundary

Implementation Agent 不得：

* 顺带实现其他 Txxx
* 提前开始下一 Wave
* 无关重构
* 修改其他 Task definitions
* 修改 `TASK_GRAPH.md`
* 修改 approved ADR
* 修改冻结设计
* 自行改变 architecture

如果实现必须违反 approved design：

报告：

`DESIGN_DEVIATION`

如果发现 approved design 缺失必要决策：

报告：

`DESIGN_GAP`

然后停止相关实现。

---

## Task 状态与 Orchestrator

Task scheduling 和 Task status 属于 Implementation Orchestrator 的职责。

普通 Implementation Agent 不应自行修改 Task Graph 状态。

标准状态包括：

* `PLANNED`
* `READY`
* `IN_PROGRESS`
* `IMPLEMENTED`
* `REVIEW_PASSED`
* `REVIEW_FAILED`
* `INTEGRATED`
* `ACCEPTED`
* `BLOCKED`

只有经过独立 Review 的 Task 才能进入后续 integration。

---

## 独立 Review

Implementation Agent 完成 Task 后，应由独立 Reviewer 检查。

Reviewer 应根据：

* Task specification
* acceptance criteria
* relevant ADR
* implementation diff
* tests
* implementation report

独立判断。

Review 结果使用：

* `REVIEW_PASSED`
* `REVIEW_FAILED`
* `DESIGN_DEVIATION`

Reviewer 不应仅因为 Implementer 声称完成就判定 PASS。

---

## Wave Integration

只有当前 Wave 所有 Task 均：

`REVIEW_PASSED`

后才允许 integration。

Integration 后应执行 Task Graph 和 repository 要求的项目级 validation。

Wave 结果记录在：

`docs/implementation/reports/WAVE-XX.md`

当前执行一个 Wave 后默认停止。

除非用户明确批准继续，否则不要自动进入下一 Wave。

---

## 中文与报告语言规范

本项目所有 implementation documentation、Agent report、Review report、Integration report 和面向人的执行汇报默认使用：

**简体中文**

以下内容必须使用中文：

* Task 实施摘要
* Implementation report
* Review 说明
* Review findings
* 验收结果说明
* Integration report
* Wave report
* 风险说明
* Blocker 说明
* Validation 说明
* Migration 说明
* API compatibility 说明
* `DESIGN_GAP` 解释
* `DESIGN_DEVIATION` 解释
* Orchestrator 最终汇报

以下内容保持原样，不翻译：

* shell commands
* source code
* file paths
* API paths
* environment variables
* identifiers
* class names
* function names
* package names
* framework names
* library names
* tool names
* protocol names
* machine-readable status enums

例如以下名称保持英文：

* FastAPI
* PostgreSQL
* Celery
* Redis
* SSE
* Docker Compose
* NestJS
* Prisma
* Vue
* Vite
* pnpm
* uv
* Ruff
* mypy
* pytest
* Vitest

---

## 状态枚举

机器可读状态必须保持英文。

不得创建中文替代状态。

包括：

* `PLANNED`
* `READY`
* `IN_PROGRESS`
* `IMPLEMENTED`
* `REVIEW_PASSED`
* `REVIEW_FAILED`
* `INTEGRATED`
* `ACCEPTED`
* `BLOCKED`
* `PASS`
* `FAIL`
* `DESIGN_GAP`
* `DESIGN_DEVIATION`

推荐：

`Review 结果：REVIEW_PASSED`

`Integration 结果：PASS`

`下一 Wave：READY`

允许增加中文说明，例如：

`REVIEW_FAILED（API compatibility 验收项未满足）`

但不得改变标准枚举值。

---

## Implementation Report 规范

以下报告默认使用简体中文：

* `docs/implementation/reports/Txxx.md`
* `docs/implementation/reports/WAVE-XX.md`

报告正文至少应根据适用情况包含：

* Task / Wave ID
* 实施结果
* 实施摘要
* 修改文件
* API contract impact
* database / migration impact
* tests
* validation commands
* validation results
* acceptance criteria
* Review result
* Integration result
* 风险
* blocker
* `DESIGN_GAP`
* `DESIGN_DEVIATION`
* 下一 Wave readiness

示例：

```markdown
# Wave 1 实施报告

## 执行结果

Wave：Wave 1

Task：

- T001 — Python backend workspace bootstrap

Review 结果：REVIEW_PASSED

Integration 结果：PASS

## Validation

- `uv sync --frozen`：PASS
- `uv lock --check`：PASS
- Ruff：PASS
- mypy：PASS，共检查 16 个文件
- pytest：PASS，共 2 个测试

## API / Migration / Docker

本 Task 未涉及。

## Blocker

无。

## Design

新增 `DESIGN_GAP`：无。

`DESIGN_DEVIATION`：无。

## 风险

非交互 shell 默认没有将 mise 安装的 `uv` 加入 `PATH`。

使用 `uv` 的绝对路径后全部 validation 均通过。

当前不阻塞后续 implementation。

## 下一 Wave

Wave 2 / T002：READY

本次未启动 Wave 2。
```

---

## Agent 输出原则

所有 Agent 应遵守以下原则：

1. 项目事实存在 repository，而不是 conversation memory 中。
2. 每个 Task 必须可以由新的 Agent 从 repository 独立恢复上下文。
3. 标准状态保持 machine-readable。
4. 人类说明默认使用简体中文。
5. 不允许根据聊天历史补全缺失的架构决定。
6. 不允许 Implementation Agent自行扩大 Task scope。
7. 不允许为了通过测试破坏 compatibility、security 或 approved architecture。
8. 如果报告和聊天总结不一致，以经过 Review / Integration 后 repository 中的最终状态和报告为准。
