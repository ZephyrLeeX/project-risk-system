# Reviewer Protocol

只 Review 一个 assigned Task。

## 默认读取

只读取：

- assigned `Txxx.md`
- Task 明确引用的 ADR / design section
- implementation diff
- tests
- Task report

不要默认读取整个项目历史。

## 检查

- scope compliance
- acceptance criteria
- correctness
- relevant architecture/contract compliance
- tests / validation
- 是否存在明显 regression

结果：

- `REVIEW_PASSED`
- `REVIEW_FAILED`
- `DESIGN_GAP`
- `DESIGN_DEVIATION`

如果失败，只输出具体 findings。

格式：

`F1 [HIGH|MEDIUM|LOW]`
- location
- violated requirement
- reason
- required fix

不要重新写长篇 implementation summary。
