# 统一 Python 后端工程基线

FastAPI 后端统一使用 Python 3.12、Pydantic v2、SQLAlchemy 2、Alembic、psycopg 3、Celery、Redis、pytest、Ruff 和 mypy，并使用 uv 管理依赖与锁文件。不采用 SQLModel，避免复杂关系、迁移和 PostgreSQL 特性受到额外 ORM 抽象限制。

## Consequences

- 开发、CI、测试和 Docker 镜像必须使用同一依赖锁文件。
- 数据库结构只能通过 Alembic 迁移演进；应用启动不得隐式创建或修改正式表结构。
- Ruff、mypy、测试、迁移检查和镜像构建是合并与发布的质量门槛。
