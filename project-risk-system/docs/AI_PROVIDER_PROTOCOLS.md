# AI Provider 协议配置

Provider 的 `endpoint` 始终是 API Base URL；不要填写最终操作路径。协议由 `protocol` 显式决定，不能由厂商名或模型名推断。

| protocol | Base URL 示例 | 操作路径 | 认证 |
| --- | --- | --- | --- |
| `OPENAI_CHAT_COMPLETIONS` | `https://api.deepseek.com/v1` | `chat/completions` | `Authorization: Bearer` |
| `OPENAI_RESPONSES` | `https://token.longshine.com:18443/v1` | `responses` | `Authorization: Bearer` |
| `ANTHROPIC_MESSAGES` | `https://api.anthropic.com/v1` | `messages` | `x-api-key` 与 `anthropic-version: 2023-06-01` |

示例：

```text
protocol=OPENAI_CHAT_COMPLETIONS
endpoint=https://api.deepseek.com/v1
model=<实际模型>

protocol=OPENAI_RESPONSES
endpoint=https://token.longshine.com:18443/v1
model=<该网关实际提供的 Codex 模型>

protocol=ANTHROPIC_MESSAGES
endpoint=https://api.anthropic.com/v1
model=<实际 Claude model>
```

内部网关仍需同时满足 hostname 和 CIDR allowlist：

```text
AI_OUTBOUND_ALLOWED_HOSTNAMES=token.longshine.com
AI_OUTBOUND_ALLOWED_CIDRS=10.0.0.1/32
```

所有协议均执行 DNS 解析与连接前重验、TLS 证书验证并禁止重定向。API Key、提示词与原始 Provider 响应均不会写入调用日志或审计记录。
