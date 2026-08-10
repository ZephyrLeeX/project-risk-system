# 第三阶段第2步：API Key 管理实现说明

## 1. 本步目标

在不改变 `ui-prototype/07-api-key-management.html` 页面结构和功能内容的前提下，将正式系统的「API Key 管理」页由静态样例升级为真实数据库、真实接口和真实交互。

## 2. 已实现功能

### 2.1 AI 服务配置

- 查询全部、已启用、已停用服务；支持名称、厂商、模型和地址搜索。
- 新增、编辑 AI 服务配置。
- API Key 单独轮换；编辑时留空不会覆盖原 Key。
- 服务启用与停用。
- 默认服务切换确认；默认服务不能直接停用。
- 超时时间、失败重试次数和密钥有效期配置。
- 单服务连接测试、保存前测试、批量连接测试。

### 2.2 调用策略和有效期

- 默认服务优先显示。
- 备用服务按优先级显示，停用服务不会参与批量测试。
- 统计未来30天内到期的已启用 Key。
- 有效期卡片可直接进入密钥更新。

### 2.3 调用统计和日志

- 近7日调用总量、成功数、成功率、平均耗时、P95耗时和 Token 用量。
- 按周报分析、Agent 问答、风险提取筛选趋势。
- 调用日志按成功/失败筛选并分页。
- 调用详情仅展示 Trace ID、服务、模型、场景、Token、耗时、结果、错误摘要和操作人。
- 不保存完整邮件、提示词、模型原始响应或 API Key 明文。

### 2.4 管理概览联动

- 「管理概览」中的 AI 服务数量、健康服务数和30天内到期数改为读取真实接口。

## 3. 数据库设计

### 3.1 `ai_provider_configs`

存储服务名称、厂商、HTTPS 地址、模型、加密凭据、Key 后4位、有效期、超时、重试、启停、默认标识、优先级和最近测试结果。

### 3.2 `ai_call_logs`

存储调用元数据和脱敏错误摘要。连接测试也作为 `CONNECTION_TEST` 场景记录，用于统计和审计。

### 3.3 迁移

迁移文件：`apps/api/prisma/migrations/20260801143343_ai_provider_management/migration.sql`，已成功应用到本地 PostgreSQL。

## 4. 安全设计

- API Key 使用 AES-256-GCM 加密，密文、随机 IV 和认证标签分字段保存。
- 每次加密使用新的随机 IV；页面只返回 Key 后4位掩码。
- 前端密钥输入框使用 `autocomplete="new-password"`，防止登录密码管理器误填。
- 服务地址只允许 HTTPS，拒绝账号密码形式 URL、localhost、本机地址和内网 IP。
- 新增、编辑、轮换、启停、默认服务切换和连接测试均写入审计日志。
- 审计快照不包含 API Key 明文。

## 5. API 清单

| 方法 | 地址 | 用途 |
|---|---|---|
| GET | `/api/admin/ai-services/summary` | 汇总指标 |
| GET | `/api/admin/ai-services` | 服务列表与筛选 |
| POST | `/api/admin/ai-services` | 新增服务 |
| PATCH | `/api/admin/ai-services/:id` | 编辑服务 |
| POST | `/api/admin/ai-services/:id/rotate-key` | 轮换 Key |
| POST | `/api/admin/ai-services/:id/status` | 启用/停用 |
| POST | `/api/admin/ai-services/:id/set-default` | 设为默认服务 |
| POST | `/api/admin/ai-services/:id/test` | 单服务连接测试 |
| POST | `/api/admin/ai-services/test-draft` | 保存前连接测试 |
| POST | `/api/admin/ai-services/test-all` | 批量连接测试 |
| GET | `/api/admin/ai-services/strategy` | 调用策略 |
| GET | `/api/admin/ai-services/usage` | 近7日调用统计 |
| GET | `/api/admin/ai-services/calls` | 调用日志 |
| GET | `/api/admin/ai-services/calls/:id` | 调用详情 |

全部接口要求登录且具备 `admin.ai.manage` 权限。

## 6. 验收结果

- Prisma schema 格式化、客户端生成和数据库迁移成功。
- TypeScript 类型检查通过。
- 契约、前端和后端共47项自动化测试通过；新增凭据加密和地址安全测试9项。
- 前端与 API 构建产物已生成。
- API 健康检查通过。
- 浏览器真实联调验证：新增、加密掩码、启用、默认切换、默认服务保护、连接测试失败记录、统计刷新和详情脱敏均正常。
- 联调专用配置、调用日志及其审计记录已从数据库清理，不污染业务数据。

## 7. 后续步骤

第三阶段第3步建议实现「系统配置」真实后端，包括风险等级、风险分类、邮箱识别、安全策略、通知规则、配置发布版本和变更记录。
