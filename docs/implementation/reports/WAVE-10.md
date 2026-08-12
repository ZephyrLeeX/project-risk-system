# Wave 10 Integration Report

- **Wave：** Wave 10
- **结果：** `PASS`
- **范围：** T016、T019、T020、T025；未启动 Wave 11、T026、T042
- **Integration fix checkpoint：** `7b9722075ca3bc8358789198b7ef6b0e6282fcfa`
- **Final metadata checkpoint：** `92d6338873165e2de8d98461ad9e65deaa811905`

## 集成结果

Wave 10 四个已审查 Task 的跨模块路径通过：管理首页依赖健康检查与审计读取、导入 rollback 与后续写冲突保护、dashboard scope/金额/枚举读取，以及 mailbox T024 handoff → T025 source refetch → bounded parser → project/alias matching 均保持现有模块边界。T025 helper IPC 的完整套件竞态由 Queue 改为单向 Pipe，未改变 ADR 0024 的 5 秒 timeout 或 raw-content 边界。

未启动 Wave 10 之外的任何 Task，也未处理 DG-04、DG-05、DG-08。

## Validation

| 检查项 | 结果 |
|---|---|
| Cross-module integration | `PASS` — T016/T019/T020/T025 paths covered by full suite and mailbox/import/dashboard/admin tests |
| Ruff | `PASS` |
| mypy | `PASS` — 150 source files |
| Full pytest | `PASS` — isolated PostgreSQL 16: `179 passed, 1 skipped`; local no-DB run: `136 passed, 44 skipped` |
| `uv lock --check` | `PASS` |
| `git diff --check` | `PASS` |
| Alembic / PostgreSQL 16 | `PASS` — temporary host-network PostgreSQL 16, `alembic upgrade head` completed; container removed |

## Integration fixes

- `apps/api-python/src/risk_platform/mailbox/parsing.py`：将 helper 结果通道从 `multiprocessing.Queue` 改为单向 `Pipe`，避免全量套件中的 helper 输出竞态导致合法 `.txt` 附件被误判为 `PARSER_TIMEOUT`。安全限制、解析 allowlist、资源边界及 metadata-only 结果未放宽。

## Readiness

Wave 10 已 `PASS`。Wave 11 尚未启动；后续 readiness 仍受其自身 Task dependency 与未解决设计缺口约束。
