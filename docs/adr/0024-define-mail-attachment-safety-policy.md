# 定义邮件附件安全解析策略

状态：已批准

## Context

ADR 0007、ADR 0014、ADR 0022 与设计 §§6、7、8 要求邮件附件作为不可信输入受到类型、大小、时间和资源限制，并且不得持久化原始内容；但没有给出 T025 可以实施和测试的附件允许集合、解析方法或资源边界。本 ADR 解决该缺口，不采纳历史 NestJS 实现的库选择或阈值作为安全依据。

## Decision

### 允许的格式与识别

当且仅当文件名扩展名、声明 MIME（去除参数后小写比较）和内容识别全部一致时，T025 才解析附件。允许集合固定为：

| 格式 | 扩展名 | MIME | 内容识别 | 允许的安全解析 |
| --- | --- | --- | --- | --- |
| 纯文本 | `.txt` | `text/plain` | 严格 UTF-8（可含 BOM），无 NUL | 流式 UTF-8 解码；不解释标记、链接或嵌入指令。 |
| PDF | `.pdf` | `application/pdf` | 开头为 `%PDF-` | 固定版本、无网络能力的 PDF 文本提取器；只提取文本，不执行 JavaScript、表单、动作、嵌入文件或外部引用。 |
| Word OOXML | `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | ZIP 签名且仅按下述受限 OOXML 检查存在 `word/document.xml` | 仅用受限 ZIP 读取和 `defusedxml` 解析正文 XML 文本节点；不处理宏、关系中的外部目标、嵌入对象、图像或其他 XML 部件。 |
| Excel OOXML | `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | ZIP 签名且仅按下述受限 OOXML 检查存在 `xl/workbook.xml` | 仅用受限 ZIP 读取和 `defusedxml` 解析工作表、shared strings 和 inline strings 的显示文本；不计算公式、不运行宏、外部链接、连接、嵌入对象或图像。 |

`application/octet-stream`、缺失或不匹配的 MIME/扩展名、伪造签名，以及 `.docm`、`.xlsm`、`.doc`、`.xls`、`.csv`、压缩包和所有其他格式均不解析。解析器不得依据用户控制的文件名选择路径或命令，也不得调用 shell、网络、Office 自动化、OCR 或任何外部转换服务。

### 输入和资源上限

- 单封原始 RFC 822 邮件最大 `20 MiB`；超过上限的 source 产生 mail-stage `PERMANENT_FAILURE` (`MAIL_SOURCE_TOO_LARGE`)。
- 每封邮件最多处理 `10` 个附件；单附件压缩前字节数最大 `5 MiB`，所有附件压缩前总量最大 `15 MiB`。超过者仅生成 metadata-only 的附件终态，不读取或解析其内容。
- 单附件最多提取 `20,000` 个 Unicode 字符；正文与全部附件合并后最多向下游交付 `60,000` 个 Unicode 字符。达到输出上限时截断并记录 `OUTPUT_TRUNCATED` metadata，不保留被截断内容。
- OOXML ZIP 预检在解压前完成：最多 `200` 个条目；单条目解压后最多 `10 MiB`；总解压后最多 `25 MiB`；任一条目的压缩比不得超过 `20:1`。禁止加密 ZIP、重复/路径穿越条目和不在该格式 allowlist 内的必需读取部件。不得使用会无界解压的 convenience loader。
- PDF 最多 `200` 页；不得渲染页面或执行 OCR。无法在不超过此上限的情况下确定页数时拒绝该附件。
- MIME 结构解析的 wall-clock 上限为 `5 s`。每个附件解析在独立的、无网络 helper process 内执行，wall-clock 上限 `5 s`、CPU 上限 `3 s`、地址空间上限 `256 MiB`；整封邮件的 T025 parse stage wall-clock 上限为 `30 s`。helper 必须在 Linux 上施加等价的 CPU、地址空间和文件大小 rlimit，父进程在超时后终止并回收它；不得只依赖 Celery soft time limit。

这些限制是 T025 的固定安全配置，必须以类型化配置常量实现并在测试中断言；不得提供管理员可将其放宽的运行时配置。

### 处理结果、留存与临时文件

- 每个附件仅可持久化经过长度限制和字符清洗的文件名、规范 MIME、扩展名、压缩前字节数、允许格式、结果状态和结构化失败/截断代码。不得持久化 hash、原始字节、原始文本、提取全文、压缩内容或 parser diagnostic 中的内容片段。
- 允许将从受限、已清洗文本确定性生成的安全摘要、关键要点和为后续正式风险解释所必需的最小证据摘录交给 ADR 0007 规定的留存路径；这些派生值必须分别有长度上限，且不得等同或拼接为完整正文/附件。日志、audit、Celery/Redis payload 和异常消息仍不得包含其输入内容或原始附件内容。
- attachment 的 `UNSUPPORTED`、`TYPE_MISMATCH`、`TOO_LARGE`、`MALFORMED`、`ENCRYPTED`、`ZIP_LIMIT_EXCEEDED`、`PDF_PAGE_LIMIT_EXCEEDED`、`PARSER_TIMEOUT`、`PARSER_RESOURCE_LIMIT` 或 `OUTPUT_TRUNCATED` 必须以无内容的结构化 metadata 表示；不得把解析错误原文作为摘要保存或记录。
- raw source 与附件仅存在于当前 Worker 内存或该 task 专属、权限为 owner-only 的临时目录。临时目录名必须为随机值，任何附件名不得参与路径构造。每次 helper 调用后立刻删除其输入/输出，task 的 `finally` 路径在成功、失败、取消和超时中递归删除 task 目录；Worker 启动和定期 reconciliation 删除超过 `1 h` 的遗留目录。临时文件从不作为恢复依据。

### 失败、重试与 ADR 0022 handoff

- source 缺失、UID 无效或 UIDVALIDITY 变化继续完全按照 ADR 0022 记为 mail-stage `PERMANENT_FAILURE`。
- 附件不支持、类型不匹配、超限、损坏、加密或达到 PDF/ZIP/输出安全限制，是该附件不可重试的 terminal outcome。只要 MIME 结构与正文处理完成，T025 parse stage 记为 `SUCCEEDED`，同时留存相应的 metadata-only 附件结果；它们不得通过无限 retry 试图绕过安全边界。
- helper 启动失败、基础设施资源限制异常、IMAP 重抓的瞬时失败，或在任何有效附件开始前发生的可恢复解析运行时故障，记为 `RETRYABLE_FAILURE`，由 ADR 0018 的有限、退避 durable retry 处理。每次 retry 必须按 ADR 0022 source identity 重新抓取，且 payload 不得带 raw source。
- 已耗尽 ADR 0018 的 retry policy 后，可恢复运行时故障转为结构化 mail-stage `PERMANENT_FAILURE` (`PARSER_RETRY_EXHAUSTED`)；不得伪造成 `SUCCEEDED`。T025 不自行定义新的 task 状态、outbox 或 cursor 规则，终态与 batch/cursor handoff 仍由 ADR 0022 决定。

## Consequences

- T025 可在固定、可测试的 input/resource 边界内实现，且旧附件格式或历史解析器不会扩大生产攻击面。
- 加密、损坏或不支持的附件可能不贡献风险文本，但会留下不含内容的可解释处理事实；邮件正文仍可继续参与项目匹配。
- PDF 与 OOXML 解析需要受限 helper 和明确的安全测试（伪造 MIME、zip bomb、加密/损坏、timeout、resource limit、临时文件清理），而不是直接在 Celery Worker 中加载不受限文档。
