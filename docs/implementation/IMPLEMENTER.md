# Implementer Protocol

一次只执行一个 assigned Task。

## 默认读取

只读取：

- `AGENTS.md`
- `docs/implementation/EXECUTION_STATE.md`
- assigned `Txxx.md`
- Task 明确引用的 ADR / design section
- 当前实现所需代码

不要默认读取：

- 其他 Task
- 历史 Wave reports
- 全部 ADR
- 完整 design
- 完整 CONTEXT

除非当前 Task 明确需要。

## 规则

- 严格遵守 Task scope。
- 不提前实现其他 Task。
- 不修改 `TASK_GRAPH.md`。
- 不修改冻结设计或 ADR。
- 不进行无关重构。
- DESIGN 不足时报告 `DESIGN_GAP`。
- 必须违反批准设计才能继续时报告 `DESIGN_DEVIATION`。

## 完成后

执行 Task 要求的 validation。

输出简短 Task report：

- Status
- Changed
- Validation
- Contract/Migration impact
- DESIGN_GAP / DESIGN_DEVIATION
- Risks

正常 PASS report 控制在约 30 行以内。
