# FastAPI 后端继续使用 PostgreSQL

FastAPI 重写将继续使用 PostgreSQL，而不采用最初设想的 SQLite。系统需要支持多人操作、批量 Excel 导入、邮箱同步、后台任务和不可篡改审计；保留 PostgreSQL 可以延续现有数据模型与数据库约束，并避免 SQLite 单写者模型和应用层审计带来的限制。

## Consequences

- 新的数据访问层和迁移工具以 PostgreSQL 为正式运行环境。
- 本地、测试和部署环境应尽量使用同一种数据库，避免 SQLite 与 PostgreSQL 行为差异。
- 可以重新设计 Python ORM 模型，但必须保留已确认的业务约束和前端可观察行为。
