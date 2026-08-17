这版的核心原则是：**首版只做 DeepSeek Official，彻底重构 Agent Core，优先采用 DeepSeek 官方原生 Chat Completions + Tool Calls；同时把 Provider Adapter 边界设计好，为未来 Company API Adapter 留扩展点。** DeepSeek 官方当前的 OpenAI 格式接口为 `/chat/completions`，官方模型支持 Tool Calls；官方也提供 `/models` 用于枚举可用模型。([DeepSeek API 文档][1])

---

# AI Agent 重构需求说明书 v1.0

## 1. 产品定位

AI Agent 是**项目风险管理系统内的业务智能助手**，不是通用聊天机器人。

V1 只能处理当前系统中的：

```text
项目
风险
待办
周报
项目状态
风险分析
项目风险建议
相关业务数据
```

允许：

```text
锡山智慧城市一期有哪些风险？

无锡有哪些项目需要关注？

这个项目有哪些风险值得上报？

帮我上报这三个风险。

把这个风险调整成高风险。

给这个风险增加两个处理待办。

把这个待办改成下周五完成。

解除这个风险。

把这个项目调整到完成状态。
```

不允许：

```text
什么是风险管理？

怎么做项目管理？

帮我写 Python。

今天的天气怎么样？

写一份通用项目管理教程。
```

V1 Scope：

```text
SYSTEM_DATA_ONLY
```

但架构必须允许未来扩展：

```text
PROJECT_KNOWLEDGE
```

届时可以回答通用项目管理知识，而不需要重写 Agent Core。

---

# 2. 首版 Provider 范围

V1：

```text
✅ DeepSeek Official Adapter
❌ Company API Adapter
```

Company API 首版完全不进入实现范围。

但是架构必须是：

```text
Agent Core
    │
    ▼
AiProviderAdapter
    │
    ├── DeepSeekOfficialAdapter     V1
    │
    ├── CompanyApiAdapter           Future
    │
    ├── OpenAIAdapter               Future
    │
    └── OtherAdapter                Future
```

Agent Core 不允许出现：

```text
DeepSeek 特有 HTTP 字段判断
公司网关 500 not implemented 判断
Codex Responses item 判断
某个 Provider 特有错误代码
```

这些必须全部封装在 Provider Adapter 内。

---

# 3. DeepSeek Official Adapter

V1 使用 DeepSeek 官方 API。

核心方向：

```text
DeepSeek Official
→ Chat Completions
→ Native Tool Calls
→ Agent Tool Loop
```

DeepSeek 官方当前 `/chat/completions` 接口接受 system/user/assistant/tool 等消息，并支持原生 Tool Calls。([DeepSeek API 文档][1])

不再要求 DeepSeek 输出：

```text
AGENT_PROVIDER_EXECUTION_V2
```

这种项目内部 JSON 才能调用工具。

旧协议可以删除或作为历史兼容代码逐步退出，但**新 DeepSeek Agent Core 不得依赖它**。

---

# 4. Provider Account 与 Model Config 分离

数据模型改为两层。

## Provider Account

例如：

```text
DeepSeek Official

providerType = DEEPSEEK_OFFICIAL
apiKey = encrypted
enabled = true
```

DeepSeekOfficialAdapter 只能连接 DeepSeek 官方服务。

不要把公司网关地址塞进 `DEEPSEEK_OFFICIAL` 伪装成 DeepSeek。

DeepSeek 官方当前 OpenAI 格式基础地址是官方 `api.deepseek.com`。([DeepSeek API 文档][2])

---

## Model Config

一个 Provider Account 可以配置多个模型：

```text
DeepSeek Official
│
├── Model A
│   ├── modelName
│   ├── enabled
│   ├── isDefault
│   ├── priority
│   ├── timeout
│   └── health
│
├── Model B
│
└── Model C
```

**禁止在代码中硬编码业务默认模型名称。**

后台配置什么，就使用什么。

DeepSeek 官方当前 `/models` 接口本身也提供模型 ID 枚举能力。([DeepSeek API 文档][3])

---

# 5. 模型选择

顺序：

```text
enabled = true
↓
当前可用
↓
isDefault = true 优先
↓
priority ASC
↓
id ASC 保证稳定排序
```

一次 Agent Turn 开始时应取得一个**稳定的候选模型顺序快照**。

不能一个 Turn 执行到一半，因为后台有人改 priority，就突然改变顺序。

---

# 6. Model Failover

同一个模型可以先执行有限的 transport retry。

仍失败后再 failover。

允许切下一个模型：

```text
network error
connect timeout
read timeout
429
500
502
503
其他明确 transient 5xx

404 / model not found
```

404 同时应标记当前 Model Config 存在配置问题。

不允许通过换模型掩盖：

```text
401
403
400
schema error
协议实现错误
业务校验错误
Tool 错误
RBAC 错误
程序内部错误
```

401 / 403：

```text
Provider Account credential/config error
```

400 / schema / protocol：

```text
configuration or implementation error
```

不能“换一个模型试试看”把真正 bug 隐藏掉。

---

# 7. Agent 总流程

逻辑流程：

```text
用户输入
   ↓
① Scope / 业务范围判断
   ↓
② Intent + Entity 理解
   ↓
③ 判断当前信息是否充分
   ↓
④ DeepSeek Tool Call
   ↓
⑤ Tool Executor
   ↓
⑥ Tool Result
   ↓
⑦ 再次进入 DeepSeek
   ↓
是否还需要 Tool？
   ├── YES → 继续 Tool Loop
   └── NO
        ↓
是否需要用户补充/选择？
   ├── YES → WAITING_FOR_USER
   └── NO
        ↓
是否为写操作？
   ├── YES → WRITE_CONFIRMATION
   └── NO
        ↓
最终回答
```

---

# 8. Scope Guard

Agent 首先必须判断：

> 用户的问题是否属于当前系统项目业务范围。

禁止：

```text
先调用一堆 Tool
→ 最后才发现问题不属于系统
```

Scope Decision 至少区分：

```text
ALLOWED
OUT_OF_SCOPE
```

未来可以增加：

```text
PROJECT_KNOWLEDGE
```

因此 Scope Policy 必须是独立组件，不允许散落在 system prompt 里。

---

# 9. Out-of-Scope 行为

如果问题不允许：

```text
不调用业务 Tool
不查询项目数据
不进入写流程
```

直接给予固定、简短的业务范围说明。

例如：

> 我目前只能处理本系统内的项目、风险、待办、周报等项目业务问题。

---

# 10. Tool Loop

不再使用固定：

```text
PLAN
→ Tool
→ RESPOND
```

而采用有上限 Tool Loop：

```text
DeepSeek
   ↓
tool_calls
   ↓
Tool Executor
   ↓
tool results
   ↓
DeepSeek
   ↓
tool_calls?
```

直到：

```text
final response
interaction required
安全上限触发
执行失败
```

DeepSeek 官方支持原生 Tool Calls，因此这种结构与官方接口能力一致。([DeepSeek API 文档][1])

---

# 11. Tool Loop 安全限制

必须存在明确限制：

```text
max_model_rounds
max_tool_calls
max_parallel_tool_calls
max_total_execution_time
max_single_tool_result
max_total_tool_result
max_context_size
```

还必须防止：

```text
tool A(arguments X)
→ 相同 tool
→ 相同 arguments
→ 没有新增上下文
→ 无限重复
```

重复调用达到阈值必须终止。

具体默认数字属于技术实现，由 Codex 根据测试提出，但必须：

```text
配置化
有合理默认
有测试
```

不能无限循环。

---

# 12. Tool 架构

统一：

```text
DeepSeek Tool Call
       ↓
Agent Tool Registry
       ↓
Pydantic Input Validation
       ↓
Tool Authorization
       ↓
RBAC
       ↓
Data Scope
       ↓
Domain Service
       ↓
Typed Tool Result
```

AI 永远不能：

```text
直接 SQL
直接 ORM update
自己生成数据库权限
绕过 Domain Service
绕过 RBAC
```

---

# 13. Query Tools

V1 至少覆盖：

```text
project_search
project_detail

risk_category_list
risk_list
risk_detail

todo_list
todo_detail

dashboard_summary
dashboard_focus

weekly_report
weekly_report_detail
```

可以根据现有 Domain Service 做合理拆分，但业务能力不能缺失。

---

# 14. 项目识别

用户不需要知道 project UUID。

允许自然表达：

```text
锡山智慧城市一期
锡山项目
新吴项目
昆山项目
鹿路通
无锡项目
市数据局项目
```

项目最终必须通过 Tool 解析成真实：

```text
projectId
projectName
```

---

# 15. 地域语义识别

允许 AI 使用语义/地理知识辅助项目识别。

例如用户：

> 无锡有哪些项目？

AI 可以推断：

```text
锡山
新吴
惠山
锡东
```

可能与“无锡”有关。

但是有一条硬约束：

> **最终任何候选项目都必须来自当前系统 Tool 返回的真实、当前用户有权限访问的 Project。**

模型不能因为自己知道一个地名，就凭空生成不存在的项目。

---

# 16. 唯一项目

如果 Tool 结果足以唯一识别：

```text
自动选定
```

无需用户确认。

---

# 17. 多项目消歧

多个合理候选时：

```text
AgentExecution
→ WAITING_FOR_USER
→ PROJECT_SELECTION
```

前端：

```text
请选择项目：

○ 锡山智慧城市一期
○ 锡东先导区CIM平台
○ 其他候选

○ 以上都不是

手动输入：
[____________________]

[确认]
```

要求：

```text
只能选择一个项目
支持“以上都不是”
支持手动输入
```

用户选择后继续原来的 Agent Task。

不要求重新提问。

---

# 18. 手动输入项目名称

手工输入不是直接相信用户提供了一个 Project。

必须重新：

```text
project_search
→ 权限过滤
→ 实体解析
```

如果仍无匹配：

> 当前系统中没有找到该项目。

不能创建虚构 projectId。

---

# 19. 描述性信息不足

例如：

> 我要上报锡山项目风险，有点问题。

如果风险事实明显不足，**不弹复杂表单**。

AI 在聊天中继续追问：

> 具体发生了什么情况？例如供应商延期、回款问题、客户需求变化等。

下一条用户消息继续当前 conversation 上下文。

---

# 20. AI 分析能力

允许 AI：

```text
总结
归纳
判断
比较
排序
风险分析
处理建议
```

但必须区分：

```text
Tool Fact
AI Analysis
AI Recommendation
```

业务事实必须来自系统 Tool。

AI 可以基于这些事实推理。

---

# 21. AI 主动风险发现

支持：

> 这个项目有什么风险值得上报？

Agent 可以查询：

```text
项目
现有风险
待办
周报
项目状态
回款情况
系统中其他可用业务信息
```

然后提出：

```text
Candidate Risk
```

---

# 22. 禁止无依据生成候选风险

明确禁止：

```text
没有系统数据依据
+
只凭模型常识
=
创建候选风险
```

例如系统里完全没有回款异常数据，不能因为：

> 政府项目一般回款慢

就建议新增回款风险。

---

# 23. 候选风险依据

每个候选风险必须带：

```text
title
description
level
category
evidenceSummary
source/tool provenance
```

确认界面**必须展示依据**。

例如：

```text
候选风险：回款延期

依据：
第一年质保款 551.67 万已经延期；
当前项目回款记录显示……
```

不需要把 Tool JSON 原样展示给用户。

---

# 24. 批量候选风险

支持：

```text
候选风险 A
候选风险 B
候选风险 C
```

用户可以：

> 三个都上报。

进入一次批量确认界面。

用户可以：

```text
修改每个风险
取消其中某些风险
最终一次点击确认
```

---

# 25. 批量创建事务语义

已经确认采用：

```text
PARTIAL SUCCESS
```

不是整体事务。

例如：

```text
A → 成功
B → 业务校验失败
C → 成功
```

最终：

```text
A 已上报
B 上报失败：明确业务原因
C 已上报
```

不能因为 B 失败回滚 A/C。

但是**每一个单独风险自己的创建事务必须保持原子性**。

---

# 26. Risk Create

AI 可以生成：

```text
project
title
description
level
category
evidence
suggestion
```

这些都是：

```text
Draft
```

用户确认前绝不落库。

---

# 27. Risk Source

Agent 创建且经过用户人工确认的 Risk：

```text
sourceType = AGENT
```

需要给当前 `RiskSourceType` 新增：

```text
AGENT
```

目前代码中只有 `EXCEL / LITIGATION / MAIL_AI / MANUAL`，因此这需要正式数据库迁移。

Reporter：

```text
仍然是点击确认的真实用户
```

AI 不是 reporter。

---

# 28. Risk Create 自动 Todo

保留现有业务：

```text
创建 Risk
→ 自动生成一个默认处理 Todo
```

当前 `RisksService.create()` 本身就会在新建 Risk 后调用 Todo 服务创建对应处理待办。

这个行为继续保留。

---

# 29. 一个 Risk 多个 Todo

新规则：

```text
Risk
├── 默认处理 Todo
├── Todo A
├── Todo B
└── Todo C
```

当前数据库：

```text
action_items.riskId UNIQUE
```

因此现有模型实际上不允许一对多。

此次重构必须迁移为：

```text
Risk 1:N Todo
```

同时保证：

> 一个 Risk 最多只有一个系统自动创建的“默认处理 Todo”。

具体数据库实现可以使用 partial unique constraint、明确 default marker 等合适方案，但必须有 DB/Domain invariant，不能只靠代码约定。

---

# 30. Mutation 架构

强烈要求写 Tool 分成两层。

**模型永远不能获得真正的数据库 mutation tool。**

模型只能调用：

```text
risk_create_proposal
risk_update_proposal
risk_resolve_proposal

todo_create_proposal
todo_update_proposal

project_status_update_proposal
```

它们只生成：

```text
MutationDraft
```

然后：

```text
MutationDraft
↓
WRITE_CONFIRMATION
↓
用户修改
↓
用户确认
↓
Server Commit Handler
↓
Domain Service
```

真正的 Commit Handler **不进入 DeepSeek Tool Catalogue**。

这样即使模型发生 prompt injection，也无法跳过人工确认直接写数据库。

---

# 31. 所有写操作必须人工确认

硬性原则：

```text
NO CONFIRMATION
=
NO MUTATION
```

适用于：

```text
risk_create
risk_update
risk_resolve

todo_create
todo_update

project_status_update
```

---

# 32. Risk Update

允许修改：

```text
title
description
level
category
evidence
suggestion
```

禁止通过 Agent 修改：

```text
projectId
sourceType
reporter
createdAt
```

---

# 33. Risk Resolve

支持自然语言：

> 供应商问题已经处理完成，把这个风险解除。

AI 可以生成：

```text
resolutionReason
```

用户可修改。

确认后调用现有 Risk Domain Service。

当前 Risk Resolve 已经包含数据范围检查、风险状态处理、关联 Todo 联动和 Audit，因此这些 Domain Rules 必须复用，而不是在 Agent 中重新实现。

---

# 34. Todo Create

只允许：

```text
已有 Risk
→ 创建 Todo
```

禁止 Agent 创建无 Risk 的独立 Todo。

创建时可以由 AI生成：

```text
title
description
urgency
assignee
dueDate
```

全部必须确认。

---

# 35. Todo Update

允许修改：

```text
title
description
urgency
status
assignee
dueDate
completionNote
```

现有 Todo Service 已经拥有部分状态、负责人、截止日期、完成说明等业务逻辑；重构时应扩展 Domain Service，而不是把 update 规则复制进 Agent。

---

# 36. Project Status Update

支持：

```text
project_status_update
```

AI 可以建议目标状态。

用户可在确认界面修改。

最终：

```text
Project Domain Service
```

负责：

```text
currentStatus
→ targetStatus
```

是否合法。

当前项目模型中已有 `DELIVERY / COMPLETED / ARCHIVED` 等状态。

不要新增用户没有定义的新项目状态。

如果现有代码缺少集中状态转换策略：

> Codex 必须先审计现有业务行为，再把现有规则集中到 Domain Policy/Service；不能自行发明新的业务状态转换规则。

---

# 37. Interaction 模型

新增统一：

```text
AgentInteraction
```

V1：

```text
PROJECT_SELECTION
WRITE_CONFIRMATION
```

未来：

```text
FORM_INPUT
CHOICE
MULTI_CHOICE
DATE_PICKER
APPROVAL
...
```

---

# 38. Agent Execution State

至少：

```text
RUNNING
WAITING_FOR_USER
COMPLETED
FAILED
CANCELLED
```

其中：

```text
WAITING_FOR_USER
```

是 Agent 业务状态。

不要误用：

```text
DurableTask RETRY_WAIT
```

表示“等用户”。

---

# 39. WAITING_FOR_USER

等待期间：

```text
不占 Worker
不调用 DeepSeek
不计算 Provider timeout
不计算 Agent Tool Loop timeout
可以跨页面刷新
可以稍后继续
```

必须持久化：

```text
conversation
intent
interaction
candidate options
draft
必要 context
```

---

# 40. Conversation API

保留当前：

```text
POST /agent/conversations

POST /agent/conversations/{id}/messages

GET /agent/conversations/{id}

GET /agent/conversations/{id}/messages
```

当前前端已经围绕这组 API 工作，所以不应该因为 Provider 重构而破坏。

---

# 41. Interaction API

新增：

```text
POST /agent/interactions/{interactionId}/respond
```

支持：

```text
SELECT
MANUAL_INPUT
CONFIRM
CANCEL
```

WRITE_CONFIRMATION 允许提交用户编辑后的最终字段。

---

# 42. Interaction 幂等与安全

Interaction 必须：

```text
属于当前用户
属于当前 conversation
一次有效
有状态
防重复确认
防 replay
```

确认执行前必须再次检查：

```text
RBAC
data scope
resource state
risk category validity
target status
business rules
```

不能信任创建 Draft 时的旧权限状态。

---

# 43. SSE

继续兼容：

```text
progress
message.delta
completed
error
heartbeat
```

新增：

```text
interaction.required
interaction.resolved
```

当前前端 SSE reducer 已经把 event parsing/state reduction 分离出来，因此适合做这种向后兼容扩展。

---

# 44. Interaction 与 SSE 生命周期

推荐：

```text
RUNNING
↓
interaction.required
↓
SSE 正常结束
↓
WAITING_FOR_USER
```

而不是让 SSE 一直挂几小时等待用户点击。

用户提交 Interaction：

```text
POST interaction/respond
↓
enqueue/resume Agent
↓
返回 streamUrl
↓
前端重新连接 SSE
```

这也彻底避免：

```text
用户思考时间
→ SSE idle timeout
```

被误认为业务失败。

---

# 45. 用户自由文本追问

如果只是描述信息不足：

```text
AI:
请补充供应商目前具体发生了什么问题？

completed
```

下一条用户消息：

```text
四家供应商都不接受核减方案。
```

Conversation Core 应能根据持久化上下文理解这是上一问题的补充。

---

# 46. RBAC

所有 Tool：

```text
100% 当前登录用户权限
```

规则：

```text
UI 看不到
→ Agent 看不到

UI 不能修改
→ Agent 不能修改
```

没有：

```text
Agent superuser
system bypass
后台 AI 特权
```

---

# 47. Tool Result Grounding

所有业务事实必须来源于 Tool。

每个 Tool Invocation 应有内部：

```text
toolInvocationId
toolName
dataAsOf
result
```

候选风险可以保存：

```text
evidenceSummary
sourceInvocationIds
```

用户不需要看到内部 JSON，但系统必须能证明：

> 本候选风险来自哪些系统查询。

---

# 48. Provider 错误和业务状态分离

必须彻底区分：

```text
ProviderTransportError
ProviderAuthenticationError
ProviderModelUnavailable
ProviderRateLimited

AgentScopeRejected
AgentLoopLimitExceeded
AgentGroundingError

ToolValidationError
ToolAuthorizationError
ToolExecutionError

InteractionRequired
InteractionExpired

BusinessValidationError

InternalError
```

不能再出现：

```text
任何 RuntimeError
→ AGENT_EXECUTION_CONFIG_INVALID
```

这种兜底误分类。

---

# 49. SSE 不修改业务执行结论

SSE 是 delivery channel。

禁止：

```text
HTTP stream idle
→ 写一个业务 ERROR
→ 后台继续执行
```

业务最终状态只能由 Agent Execution 决定。

---

# 50. Audit

所有 mutation 必须记录：

```text
actorUserId
conversationId
turn/executionId
traceId
operation
resourceType
resourceId
result
timestamp
channel = AGENT
```

Domain Audit 继续负责实际业务写入。

Agent Audit 记录：

> 用户通过 Agent 确认了什么操作。

---

# 51. 日志安全

技术日志允许：

```text
provider type
model config id
model name
tool name
interaction type
execution id
trace id
HTTP status
latency
token usage
error classification
```

禁止：

```text
API Key
Authorization
完整 prompt
完整 Tool Result
完整 Provider Response
大量项目业务正文
```

---

# 52. 旧数据迁移

现有 Provider 数据不能被破坏。

重构过程中：

```text
旧 AI provider config
```

可以作为 legacy 数据保留。

首版 DeepSeek Agent：

```text
只读取新的 DEEPSEEK_OFFICIAL Provider Account + Model Config
```

不要把现有公司 API 配置自动迁移成：

```text
DEEPSEEK_OFFICIAL
```

否则会再次污染 Provider 边界。

---

# 53. DeepSeek 技术基线

目前官方文档确认：

* OpenAI 格式使用 `/chat/completions`；
* Tool Calls 原生支持；
* Tool Calls 支持模型发起 function 调用；
* `/models` 可以返回当前可用模型；
* JSON Output 也存在，但官方明确提示偶尔可能返回空 content，因此不应该把整个 Agent 又重构成“所有控制逻辑完全依赖模型手写 JSON”。([DeepSeek API 文档][3])

因此 V1：

> **Native Tool Calls 是主路径，JSON Output 只用于真正适合结构化分类的辅助场景，不作为 Tool Orchestration 核心协议。**

---