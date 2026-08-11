# Orchestrator Protocol

目标：一次只推进一个可执行工作单元。

## 默认读取

只读取：

- `AGENTS.md`
- `docs/implementation/EXECUTION_STATE.md`
- `docs/implementation/TASK_GRAPH.md`

不要默认读取历史 Wave reports、全部 ADR、完整 design 或 CONTEXT。

仅在出现冲突、状态不明确或 Task 明确引用时按需读取。

## 执行流程

1. 从 `EXECUTION_STATE.md` 恢复当前状态。
2. 从 `TASK_GRAPH.md` 找出可执行 Task。
3. 检查 dependency 和 write-set。
4. 为每个 Task 派发独立 Implementer。
5. Implementer 完成后派发独立 Reviewer。
6. 全部必要 Review 通过后执行 Integration。
7. 更新：
   - Task report
   - Wave report
   - `EXECUTION_STATE.md`
8. 停止。

## 状态规则

正常状态保持英文：

- READY
- IMPLEMENTED
- REVIEW_PASSED
- REVIEW_FAILED
- BLOCKED
- PASS
- FAIL
- DESIGN_GAP
- DESIGN_DEVIATION

## 异常处理

只有出现以下情况才扩大上下文读取：

- DESIGN_GAP
- DESIGN_DEVIATION
- REVIEW_FAILED
- Integration failure
- repository state conflict

## Git Checkpoint

每个工作单元在满足以下条件后必须创建 checkpoint commit：

- Implementation 完成；
- Independent Review = `REVIEW_PASSED`；
- 当前可执行 validation 已通过；
- skipped / unavailable validation 已在 Task report 明确记录；
- 无 unresolved `DESIGN_DEVIATION`。

提交范围必须仅包含当前工作单元及其报告/状态更新，不得混入无关 working-tree 变更。

推荐 commit subject：

`backend: complete Txxx <short description>`

如果工作树存在与当前 Task 无关的 staged / unstaged 修改，不得使用 `git add .`。
必须按文件或路径精确 stage。

commit 完成后，将 commit SHA 写入：
- Task report
- `EXECUTION_STATE.md`

只有完成 checkpoint commit 后，当前工作单元才视为可供后续 Agent 恢复的稳定状态。

正常 PASS 路径禁止重新审计整个项目历史。
