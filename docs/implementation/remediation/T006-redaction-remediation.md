# T006 Audit Snapshot Redaction Security Remediation Analysis

## Status

- 分析角色：T006 Security Remediation Analyst
- 分析结果：`ROOT_CAUSE_FOUND`
- T006 当前状态：`BLOCKED`
- 当前 blocker：仅 audit snapshot redaction security contract
- PostgreSQL audit chain：不属于当前 blocker
- Alembic migration：不属于当前 blocker
- transaction / rollback / concurrency：不属于当前 blocker
- `DESIGN_GAP`：`NO`
- `DESIGN_DEVIATION`：无
- application source code 修改：无
- T006 tests 修改：无
- `TASK_GRAPH.md` / ADR 修改：无
- Wave 4 Integration：`NOT_STARTED`
- Wave 5：未启动

本文是 remediation 分析，不是新的 Task definition，也不批准 implementation 或 Integration。

## Evidence

### Authoritative sources

| 来源 | 与 redaction 有关的约束 |
| --- | --- |
| `docs/implementation/tasks/T006-audit-chain-core.md` | redaction 属于 T006 scope；snapshot 必须 redacted；必须交付 redaction tests。 |
| `docs/fastapi-backend-design.md` §6 | 不长期保存完整邮件和附件；仅保存安全摘要、关键要点、必要证据摘录和附件 metadata。 |
| `docs/fastapi-backend-design.md` §7 | audit snapshot 不得保存完整密钥、邮件、附件文本、prompt 或模型原始响应；附件和模型输出是不可信输入。 |
| ADR 0008 | 每个敏感写入/失败 audit event 必须携带 actor、resource、result、trace ID 和 redacted snapshot。 |
| ADR 0014 | audit snapshot 不得包含完整密钥；附件和模型输出是不可信输入。 |
| ADR 0015 | 保持成熟 audit schema 兼容；本 remediation 不改变 schema 或 migration contract。 |
| `GLOBAL_CONSTRAINTS.md` | 不得通过 response/log/audit/error 泄漏 secret、mail body、attachment text、prompt 或 raw model response。 |

### Repository state and review evidence

- T006 candidate 位于 `wave4-t006`，原 candidate commits 为：
  `12f5c4d`、`8b9fa2a`、`ae4a910`。
- 原 implementation、两轮修复、Blocked Task Recovery 和 recovery fix 均未获得最终
  `REVIEW_PASSED`。
- 当前正式 tests 共包含一个原始 deep/bounded test，以及 recovery 新增的 10 组 adversarial
  regressions；snapshot tests 为 11 passed。
- recovery 后完整真实 PostgreSQL validation 为 98 passed；append、20 路 concurrency、rollback、
  `UPDATE` / `DELETE` / `TRUNCATE` rejection、tamper detection/no-repair 均为 `PASS`。
- 最终独立 Reviewer 的额外 probes 仍为 `FAIL`，因此自动化测试通过不能证明 redaction contract
  成立。
- `docs/implementation/reports/T006.md` 和 `docs/implementation/reports/WAVE-04.md` 均记录 T006
  为 `BLOCKED`，Wave 4 Integration 为 `NOT_STARTED`。

### Existing 10 adversarial regression cases

| ID | 当前 test ID | 主要覆盖 |
| --- | --- | --- |
| R1 | `reviewer-sensitive-leakage` | `model.result`、根级 `completion` |
| R2 | `reviewer-metadata-preservation-and-lists` | mail/attachment/model list 中 payload 与邻近 metadata |
| R3 | `structured-model-response-fail-closed` | structured `LLMResponse` payload 与 metadata |
| R4 | `unknown-prompt-field-and-adjacent-metadata` | prompt unknown scalar、相邻普通 audit metadata |
| R5 | `encoded-path-and-benign-content-metadata` | encoded/bracket path、普通 `contentType` / `bodySize` |
| R6 | `secret-value-on-atypical-path` | 非典型 path 下 Authorization/private-key 强特征 value |
| R7 | `review-plural-credential-keys` | plural 与若干 credential-container suffix |
| R8 | `review-metadata-key-cannot-cloak-content-subtree` | content subtree 内 `url/name/id/status` cloak |
| R9 | `review-unbounded-percent-encoding` | 多层 percent encoding |
| R10 | `review-system-instruction-alias` | prompt alias 及 plural alias |

## Root cause

### Primary root cause

当前实现试图从任意 `Mapping[str, object]` 的 key 拼写、path token 和少量 value pattern 中反推数据
语义。输入没有 event-specific schema、字段 provenance 或显式 sensitivity label，因此 classifier
无法区分：

- 真正的 non-sensitive metadata；
- 使用 metadata-like key 包装的 sensitive payload；
- 改名后的 credential container；
- 放入 URL、邮件字符串或 unknown wrapper 中的敏感值。

这不是“还缺几个关键词”，而是 classification authority 错位：不可信 snapshot 自己的字段名被
当成了安全标签。

### Why many passing tests did not close the blocker

1. **测试是样本闭合，不是不变量闭合。** 每轮把已发现字符串加入规则和 tests 后，这些固定输入
   会通过，但没有证明 key rename、wrapper insertion、value relocation、object/list conversion 等
   等价变换保持同一安全结果。
2. **分类与 rendering 耦合。** `_sanitize` 同时做递归、分类、mask、truncate 和 error handling；
   新的 metadata exception 会直接改变敏感分类优先级。
3. **safe classification 不单调。** `_METADATA_TERMINALS` 只看 leaf token；一旦 leaf 是 `id`、
   `name`、`status`、`url` 或 `endpoint`，敏感 ancestor/descendant 可能被 safe exception 覆盖。
4. **context 不是可继承的 security state。** path 中出现已知 context 才影响 leaf；unknown
   `payload` / `data` wrapper 可以中断预期语义，或让下层 metadata terminal 重新变为 safe。
5. **key normalization 仍服务于 blacklist。** camel/snake/kebab、plural、compound 和 percent
   decoding 只扩大已知词表，无法覆盖 `Bundle`、`Store`、`Collection`、`ByProvider` 等无限语义
   变体。
6. **value detection 仅覆盖已知格式。** Bearer/Basic、PEM、`sk-`、AWS key、JWT patterns 是
   defense-in-depth，不可能识别任意 provider credential、opaque token 或普通字符串形式的 secret。
7. **协议值被当作普通字符串。** URL 的 userinfo、path、query、fragment 未结构化分类；
   `endpoint` 被认为是 metadata 后，其 query credential 随整个字符串保留。
8. **类型边界过宽。** `AuditEvent.before_snapshot` / `after_snapshot` 接受任意 mapping，没有将
   `(module, action, snapshot side)` 绑定到允许字段、类型和 transform。
9. **bounded recursion 只解决资源风险。** depth/item/string limits、非 JSON rejection 和 NaN
   rejection 是应保留的输入安全措施，但不会证明内容不敏感。

### Explicit dependency assessment

当前实现确实过度依赖：

- explicit key names：`_CREDENTIAL_KEYS`、`_COMPOUND_TOKENS`；
- keyword enumeration：context/content/metadata token sets；
- shallow/lexical path matching：ancestor/leaf token 的组合，而非 typed semantic state；
- known wrapper names：mail/model/prompt/attachment 等已知 context；
- known credential formats：少量 regex；
- fixed nesting assumptions：known context → known content leaf，或 known payload container → leaf。

递归本身并不 shallow；shallow 的是每个递归节点所使用的语义模型。

## Failure classes

| Failure class | 一般化描述 | 已有证据 | 为什么继续枚举无效 |
| --- | --- | --- | --- |
| Credential semantic variants | credential 可表现为 scalar、object、list、name/value pair、URL component，wrapper 名可任意变化。 | `apiKeyBundle`、`credentialStore`、`secretsByProvider`、`authTokenCollection` | suffix 集合没有封闭边界；新名无限。 |
| Email message/body confusion | `email` 可能是可 mask 的 mailbox address，也可能是完整 header/body 字符串或 message object。 | 非地址根级 `email` 保存完整邮件 | key 相同但 value type/semantic role 不同，key-only allow 无法区分。 |
| Endpoint/query secret | 一个整体被判定为 metadata 的 URL 内部仍可含 userinfo、query credential、敏感 path/fragment。 | `provider.endpoint?...api_key=...` | 外层 metadata classification 不能替代协议内部解析。 |
| Unknown structured wrappers | unknown object/list wrapper 可承载 sensitive subtree；下层 safe-looking leaf 可解除 context。 | `attachments[].payload.name`、`model.data.status` | `payload`、`data` 可替换为任意名称；known-wrapper matching 永远不完整。 |
| Nested/context-sensitive value | 同一 leaf 名在不同 parent、semantic role、value type 下可为 content 或 metadata。 | `bodySize` 与 `body`、structured `LLMResponse`、`model.result` | leaf token 或 ancestor token 单独都不足以决定语义。 |
| Metadata over-redaction | 粗粒度 context/content 规则会删除 audit 所需的 request ID、model name、MIME、size/count、latency 等。 | `contentType`、`textLength`、token count、response time | 扩大 deny keyword 会降低可审计性，并诱发新的 safe exception。 |
| Encoding/representation bypass | key/path 可以使用 case、separator、plural、Unicode、percent layers 或 bracket/dotted form。 | R5、R9 | normalization 只能标准化表示，不能提供语义 authority。 |
| Value relocation | 相同敏感值移到不同 path 或 metadata-like field 后结果改变。 | R6 只覆盖强格式；最终 URL finding 仍失败 | 依赖 path/key 的 classifier 不满足 location-independent deny。 |

## Authoritative contract

### Sensitive

以下数据无论位于 root、nested object、list element、known/unknown wrapper、URL component 或
metadata-like field，均不得以完整原值进入 audit snapshot：

- password、API key、access/refresh/session/auth token、Cookie、authorization code、mailbox
  auth code、private key 及其他 credential/secret；
- 完整邮件或邮件 body/content；
- attachment text、extracted content 或原始 attachment content；
- system/user prompt、instruction、conversation prompt content；
- raw model response、completion、output、choice/message content；
- 上述数据的 structured、list、encoded、URL-embedded 或 renamed representation。

“完整密钥不得进入 audit snapshot”是绝对约束，不因字段被称为 metadata 而失效。

### Metadata

ADR 0008 要求 audit event 本身保留 actor、resource、result 和 trace ID。对于 snapshot，只有满足
以下条件的数据才属于可保留 metadata：

1. 对应 event policy 明确声明该完整 path/semantic role 可保留；
2. value type 和 validator 与声明一致，而不是任意 free-form value；
3. value 内部不包含 universal sensitive data；
4. 该节点不位于已分类为 sensitive payload 的 subtree 内。

可保留类别包括：业务/请求 identifier、明确的 enum/status/role、timestamp、size/length/count、
MIME/format、已批准 model identifier、latency/duration、attachment metadata，以及不含 credential 的
受控 endpoint component。它们不是凭 key suffix 自动获得 safe 身份。

### Context

以下分类必须同时考虑 parent structure、semantic role、完整 path、value type 和 domain/protocol：

- `email`：mailbox address 可按规则 mask；非 address string、message object 或 header/body content
  必须按 mail content 处理；
- `body` / `content` / `text`：普通业务字段与 mail/attachment/model/prompt payload 的含义不同；
- `model` / `response`：model identifier 是 metadata，raw response/choice content 是 sensitive；
- `token`：token count 是 numeric metadata，auth/access token 是 credential；
- `endpoint` / `url`：origin/path/query/userinfo/fragment 必须分别分类；
- `name` / `id` / `status`：只有 schema 声明的 metadata path 才 safe；在 sensitive subtree 中仍
  sensitive。

### Unknown

无法由 event-specific policy 和 value validator明确分类的字段、object、wrapper、list 或 value 必须
fail-closed：保留 key/结构存在性的最低证据可以使用 redaction marker，但不得保留原 value。

若整个 event kind 没有已注册 snapshot policy，audit event 仍应记录 actor/resource/result/trace 等
固定字段，但 snapshot 应整体 redacted，而不是退回 generic best-effort sanitizer。

### Structure

- object：仅访问 policy 声明的 children；unknown child 的整个 value redacted。
- nested container：父节点一旦 classified sensitive，所有 descendants 保持 sensitive；child name
  不得降级。
- list：继承 list field 的 element policy；每个 element 同样递归；unknown/mixed element
  fail-closed。
- unknown wrapper：不得自动 `RECURSE_SAFE`；整个 wrapper value redacted，除非 schema 明确允许
  递归并定义 children。
- primitive 与 structured value 互换：若与 schema type 不符，fail-closed，不得借类型变化绕过。
- depth/item/string bounds：继续执行；被截断部分用 omission marker 表示，不复制原内容。

## DESIGN_GAP assessment

`DESIGN_GAP: NO`

理由：T006、Design §6/§7、ADR 0008/0014 已明确：

- 哪些类别绝对禁止进入 audit snapshot；
- audit 必须保留固定审计事实并携带 redacted snapshot；
- 非完整内容的安全摘要/必要证据/attachment metadata 可以存在；
- 不可信输入必须受限。

从这些约束可以推出 deny 优先、unknown fail-closed、只允许明确 metadata projection 的 policy。
event-specific policy registry、typed validation、URL parsing 和 classification/rendering 分离是实现该
contract 的机制，不是新的产品语义。

若未来某个业务模块要求保留尚未由其 authoritative contract 定义的 free-form 字段，应由该模块
报告自己的 `DESIGN_GAP`；T006 默认 redaction 不应自行把该字段判为 safe，也不因此形成当前 T006
的 `DESIGN_GAP`。

## Generalized redaction policy

### Policy model

为每个 `(module, action, snapshot side, policy version)` 绑定一个 typed snapshot policy。每个 policy
node 只能声明以下 action 之一：

- `DENY`：输出 redaction marker，不访问或保留 descendants；
- `TRANSFORM`：通过特定 validator/transform 生成安全派生值，例如 mask mailbox address；
- `ALLOW_METADATA`：仅在 path、type、validator 和 provenance 全部匹配时保留；
- `RECURSE_TYPED`：按明确 child/element schema 递归；unknown child/element 默认 `DENY`。

优先级固定为：

`UNIVERSAL_DENY > ANCESTOR_DENY > TYPE/PROTOCOL_VALIDATION > DECLARED_TRANSFORM > DECLARED_METADATA > UNKNOWN_DENY`

不存在 generic “key 看起来像 metadata 所以保留”的规则。

### Classification flow

1. 验证 snapshot 为有界 JSON object；拒绝非 JSON key/value 和 non-finite number。
2. 根据固定 audit event identity 选择 policy；没有 policy 时整体 redact snapshot。
3. 对每个 node 先应用 inherited sensitivity state；sensitive ancestor 直接使 subtree `DENY`。
4. 应用 universal secret detector 作为 defense-in-depth；命中时始终 `DENY`，不能被 metadata
   allow 覆盖。
5. 验证完整 path、semantic role 和 value type 是否匹配 typed policy。
6. 对 URL、mailbox address 等 protocol/domain value 先解析，再逐 component 分类。
7. 对 object/list 只按 typed child/element policy 递归；unknown wrapper 或 type confusion `DENY`。
8. 分类完成后再 rendering；renderer 只能执行 keep、mask、redact、omit，不能重新决定 sensitivity。

### Credential-like structures

- credential-bearing field 或 container 整体 `DENY`，不依赖 wrapper suffix；
- name/value pair、map、list、nested provider configuration 和 URL credential component 都按 schema
  role 分类；
- regex 只作为额外拦截，不能作为允许普通字符串保留的依据；
- credential value 移到任何 declared metadata path 后，universal deny 仍优先。

### URL / endpoint / query

- URL 必须通过标准 parser 分为 scheme、host、port、userinfo、path、query、fragment；
- userinfo 始终 `DENY`；
- query parameter 按 typed parameter policy 递归，credential parameter value `DENY`，unknown
  parameter value `DENY`；
- scheme/host/port 仅在 endpoint metadata policy 中保留；
- path/fragment 仅在明确声明和验证后保留，否则 redact；
- 解析失败时整个 URL value `DENY`。

### Mail-like structures

- mailbox address 只有在 value 通过 address validator 时执行 mask；同名非 address value 不保留；
- message body、HTML/plain content、headers-as-content 和完整 message string/object `DENY`；
- message ID、timestamp、size、MIME、attachment metadata 等只按 typed paths 保留；
- unknown mail/message/attachment child 或 wrapper `DENY`；
- safe summary/necessary evidence 必须是上游明确生成的 safe derivative 类型，不能把 raw body 改名
  为 `summary` 后直接保留。

### Metadata preservation

- metadata allowlist 是完整 path + semantic role + type/validator allowlist，不是 `id/name/status`
  suffix allowlist；
- numeric count/size/latency、enum、UUID/timestamp 等强类型 metadata 可直接验证；
- string metadata 必须使用专用 validator/transform，且仍受 universal deny；
- sensitive ancestor/subtree 永远覆盖 metadata declaration；
- benign sibling 的分类独立，不因相邻 sensitive sibling 被整体删除。

### Wrapper rename and value relocation resistance

- wrapper rename 后不匹配 schema，结果变为 `UNKNOWN_DENY`，不会变为 safe；
- 插入任意 unknown wrapper 不改变 sensitive descendant 的 inherited state；
- value 移到另一 path 后必须重新通过该 path 的 type/provenance validator；默认不保留；
- key normalization 仅用于输入一致性和拒绝 ambiguous encoding，不再承担核心语义分类。

## Security invariants

| ID | Invariant | Contract basis |
| --- | --- | --- |
| I1 | secret/credential 的完整原值不得出现在任何 snapshot output node 或嵌入式协议 component。 | Design §7、ADR 0014、global constraints |
| I2 | 完整邮件/body、attachment text、prompt、raw model response 的原值不得出现在 snapshot。 | Design §7、global constraints |
| I3 | `DENY` classification 对 descendants 单调；任何 child key、type 或 metadata label 都不能降级。 | redacted snapshot + prohibited-category absolute boundary |
| I4 | unknown field/container/list element/type mismatch 的原值不得输出。 | untrusted input boundary + fail-closed necessity |
| I5 | 只有 event policy 明确声明且通过 type/value validation 的 metadata 才能原样保留。 | ADR 0008 audit evidence + redacted snapshot |
| I6 | metadata preservation 不能覆盖 I1/I2；同一值移动到 metadata-like path 后仍不得泄漏。 | Design §7、ADR 0014 |
| I7 | object/list recursion 必须在所有深度保持相同 classification precedence，直到安全 bounds。 | T006 deep redaction deliverable + untrusted input boundary |
| I8 | unknown wrapper insertion、wrapper rename、case/separator/encoding 变化不得把 `DENY` 变成 allow。 | prohibited category independent of representation |
| I9 | URL parsing后 userinfo、credential query 和 unknown query values不得以完整原值输出。 | ADR 0014 secret prohibition |
| I10 | mailbox address mask 仅适用于 validated address；同名 mail content 不得继承 address metadata allow。 | Design §6/§7 |
| I11 | benign sibling metadata 在满足 typed policy 时应保留，避免以整块删除掩盖 security design 缺陷。 | ADR 0008 meaningful redacted snapshot |
| I12 | classification 与 rendering 结果应确定、无输入 mutation；同一输入/policy 产生相同安全 projection。 | deterministic audit evidence requirement |
| I13 | bounds、invalid JSON 和 non-finite number 处理不得回显被拒绝的原值。 | untrusted input boundary |
| I14 | audit event 无 snapshot policy 时仍记录固定 audit facts，但 snapshot 不得使用 best-effort passthrough。 | ADR 0008 |

## Adversarial test matrix

### Systematic matrix

| Category / dimensions | Expected retained data | Expected redacted data | Invariant | Existing mapping | Missing coverage |
| --- | --- | --- | --- | --- | --- |
| Credential primitive；root/nested；known/variant key | benign sibling、固定 audit facts | credential value | I1/I6 | R1、R6、R7 | arbitrary provider format、numeric credential |
| Credential structured；object/list/map/name-value；unknown wrapper | container existence marker、benign sibling | entire credential-bearing subtree | I1/I3/I4 | R7 | `Bundle/Store/ByProvider/Collection`、name/value pairs |
| Mailbox address vs mail content；primitive/object | masked validated address、message ID/time/size/MIME | non-address `email`、headers/body/full message | I2/I5/I10 | base test、R2 | root full-mail string、headers object、mixed address/body |
| Attachment；object/list；known/unknown wrapper | typed size/MIME/approved metadata | text/extracted/raw payload | I2/I3/I7 | R1、R2、R8 | `payload/data/blob/document` wrappers、mixed lists |
| Prompt/conversation；scalar/structured/list | template ID/version、role where declared | instruction/message content、unknown payload | I2/I3/I4 | R4、R8、R10 | arbitrary alias/wrapper、primitive list turns |
| Model response；scalar/structured/list | request ID、model ID、token count、latency | completion/result/choice content/raw response | I2/I3/I5 | R1、R2、R3、R8 | `data/payload/envelope` wrappers、tool output content |
| URL endpoint；userinfo/path/query/fragment；encoded/repeated params | validated scheme/host/port、explicit safe param metadata | userinfo、credential/unknown query values、unsafe component | I1/I6/I9 | 无 | 全部；最终 Reviewer 已证实缺失 |
| Known vs unknown wrapper；depth 0..limit；object/list | only schema-declared sibling metadata | unknown wrapper value/subtree | I3/I4/I7 | R3、R4、R8 | generic random wrapper insertion、final `payload/data` findings |
| Value relocation；safe/sensitive sibling；metadata-like leaf | true typed metadata | sensitive value moved to `id/name/status/url/endpoint` | I3/I6/I11 | R6、R8 | cross-path permutation、URL/email relocation |
| Key representation；case/camel/snake/kebab/dotted/bracket/Unicode/percent | equivalent benign typed metadata | equivalent sensitive value | I8 | R5、R9、R10 | Unicode confusables、mixed encodings、invalid escapes |
| Primitive/structured type confusion | value only when declared type matches | mismatched primitive/object/list | I4/I7 | R3、R8 | every policy node × wrong JSON type |
| Mixed list；known/unknown elements；nested lists | individually valid typed metadata elements | unknown/sensitive/mismatched elements | I3/I4/I7 | R2、R7 | primitive turns、heterogeneous elements、empty/oversized lists |
| Metadata over-redaction；sensitive sibling adjacency | request/message IDs、MIME、size/count、model ID、latency/status | only classified sensitive nodes | I5/I11 | R2、R3、R4、R5 | all event policies、nested benign sibling permutations |
| Bounds；depth/items/string length | bounded safe projection、omission marker | omitted content and all sensitive values | I7/I13 | base test | sensitive node exactly at/beyond every boundary |
| Invalid JSON/non-finite/invalid key | no raw rejected value | entire invalid value | I13 | base test | invalid URL/encoding、non-string keys in nested/list structures |
| Missing event policy/version | fixed actor/resource/result/trace | entire snapshot value | I14 | 无 | unknown module/action/side/version |

### Required combinatorial method

每个 semantic category 至少与以下 axes 做 pairwise 组合，而不是为每个 finding 添加一个字符串：

- depth：root、1、2、boundary、beyond-boundary；
- structure：primitive、object、list、mixed list；
- wrapper：known、unknown、renamed、inserted；
- key：canonical、variant、metadata-like、encoded；
- siblings：safe-only、sensitive-only、safe+sensitive；
- representation：plain、URL component、name/value pair、structured credential；
- policy state：known event policy、wrong type、unknown field、missing policy version。

还应增加 metamorphic/property checks：

- 对 sensitive fixture 任意改 key case/separator、插入 unknown wrapper、object/list 包装或移动到
  metadata-like path，结果仍不得包含原 secret；
- 对 benign typed metadata 添加 sensitive sibling，不得删除 benign sibling；
- 将 declared value 改为错误 JSON type，结果只能变得更保守，不能更宽松；
- 对 output 做递归 secret scan，并确认 input 中每个 prohibited sentinel 均不出现在 output。

## Proposed implementation strategy

### Retain

- `AuditService` 使用 caller-owned `AsyncSession`、只 `flush` 不 `commit` 的 transaction boundary；
- PostgreSQL append-only chain、hash canonicalization、triggers、indexes、verifier 和 migration；
- JSON-only、non-finite rejection；
- depth/object/list/string bounds 和 deterministic deep-copy behavior；
-统一 redaction marker 与 email masking transform 的概念；
- 现有 10 组 regressions 作为历史最低回归集，但不作为完整安全证明。

### Replace

- `_CREDENTIAL_KEYS` + suffix combinations 作为主 credential classifier；
- `_CONTEXT/_CONTENT/_METADATA` token sets 作为主 semantic policy；
- generic metadata terminal allow（`id/name/status/url/endpoint/...`）；
- known-wrapper/path heuristic；
- known secret regex 作为 allow/deny 的决定性依据；
- `_sanitize` 内 classification、recursion 和 rendering 混合的结构；
- unknown field 默认递归并尝试 leaf classification 的行为。

### Target structure

1. **Policy registry**：以 event identity/version 选择 typed snapshot policy。
2. **Classifier**：只产生 node decisions 和 inherited security state，不生成输出。
3. **Protocol validators**：URL、mailbox address、UUID/time/MIME/enum/count 等独立验证。
4. **Recursive projector**：按 typed object/list schema 遍历；unknown/type mismatch fail-closed。
5. **Renderer**：执行 redact/mask/keep/omit，不拥有分类例外。
6. **Defense-in-depth scanners**：credential patterns、output secret scan；只能收紧，不能放宽 policy。
7. **Versioned tests**：每个 event policy 有正向 metadata fixtures、negative sensitive fixtures、unknown
   field/type fixtures和 metamorphic tests。

这是一种 allowlist + typed + recursive policy。blacklist/pattern 仍可保留为额外 deny 层，但不能成为
核心安全模型。

未来 Reviewer finding 必须映射到 I1-I14 中的 invariant 和 matrix axis。若 finding 只能通过新增一个
特殊 key 修复，说明 remediation 仍未实现 generalized policy。

## Existing implementation portions to retain/remove

| Portion | Decision | Reason |
| --- | --- | --- |
| `AuditEvent` fixed audit fields | Retain | ADR 0008 所需 audit facts。 |
| caller-owned transaction / `flush` | Retain | 已通过 transaction/rollback validation。 |
| PostgreSQL chain/migration/verifier | Retain unchanged | 不属于 blocker。 |
| JSON/type/bounds checks | Retain, move before classification | 解决输入和资源边界。 |
| recursive traversal | Retain concept, replace traversal authority | 递归需要 typed policy，而非 token heuristics。 |
| email mask transform | Retain behind address validator | 防止非地址 mail content 继承 allow。 |
| secret regex | Retain only as deny-only defense | 格式不完整，不能证明 safe。 |
| key decoding/tokenization | Reduce to canonicalization/rejection | 不再提供核心 semantic classification。 |
| credential/context/content enumerations | Replace as primary policy | 无限语义变体造成持续 bypass。 |
| metadata terminals | Remove as generic allow | 造成 cloak 和非单调分类。 |
| `_sanitize` combined decision/rendering | Split | 防止 rendering exception 改写 sensitivity。 |

## Acceptance requirements for remediation

新的 implementation attempt 只有同时满足以下条件才可重新进入独立 Review：

1. 先形成并审查 versioned typed policy model；不得先改更多 key lists。
2. 输出默认 deny；不存在 policy、unknown field/wrapper/element 或 wrong type 时不得保存原 value。
3. I1-I14 全部有自动化 tests，且 test oracle 不复用 production classifier。
4. 现有 R1-R10 全部保留并通过。
5. 最终 Reviewer 四类 findings 有正式 regression coverage：credential semantic variants、非地址
   `email` mail content、URL query secret、unknown `payload/data` wrapper。
6. test matrix 覆盖 object/list/nesting/unknown wrapper/value relocation/protocol parsing/type confusion；
   不能只覆盖 canonical key。
7. property/metamorphic tests 证明 wrapper rename/insertion、representation change 和 value relocation
   不会把 deny 变成 allow。
8. benign typed metadata preservation 有独立正向 tests，证明 request ID、MIME、size/count、model ID、
   latency/status 等不会被无差别整块删除。
9. classification 与 rendering 分离；metadata allow 不得覆盖 universal/ancestor deny。
10. URL parser tests 覆盖 userinfo、repeated/encoded query、fragment、invalid URL 与 unknown params。
11. 输出递归 secret scan 和 prohibited sentinel non-occurrence tests 通过。
12. T006 原 PostgreSQL、Alembic、append/concurrency/mutation rejection/rollback/tamper tests 全部保持
    `PASS`，且 migration/API contract 无变化。
13. Ruff、mypy、全量 pytest、真实 PostgreSQL integration、Alembic upgrade/check/heads 全部
    `PASS`。
14. implementation report 明确记录 policy versions、covered event kinds、unknown-event behavior 和
    remaining risk。
15. 新的独立 Reviewer 主动设计未进入正式 suite 的 adversarial probes 后才能给出结论。

## Reviewer strategy

1. Reviewer 从 authoritative contract 和 I1-I14 建立自己的 oracle，不复制 implementation 的
   key sets、regex 或 registry internals。
2. 先检查 architecture：是否仍存在 generic best-effort sanitizer 或 metadata terminal shortcut；
   若存在，直接要求解释其安全边界。
3. 对每个 prohibited semantic category执行 metamorphic mutation：rename、wrap、listify、move、
   encode、embed in URL、place beside safe metadata。
4. 对每个 allowed metadata category执行反向 tests：添加 sensitive sibling、放在敏感对象附近、
   改 wrapper 名，确认只保留 schema 声明的数据。
5. 使用独立生成器产生 unknown wrappers、wrong JSON types、mixed lists、encoded keys和 URL
   components；不得只运行 repository pytest。
6. 验证 missing policy/version 时 snapshot 整体 fail-closed，同时 fixed audit facts 仍写入。
7. 对 output 做递归 prohibited sentinel scan；对 masked/transformed fields 验证不能重建原值。
8. 复跑 T006 完整 PostgreSQL/Alembic/transaction suite，确认 remediation 未回归已通过的 chain
   guarantees。
9. Review 结论只能为 `REVIEW_PASSED`、`REVIEW_FAILED`、`DESIGN_DEVIATION` 或
   `DESIGN_GAP`；正式 suite 全绿但独立 probe 泄漏时必须为 `REVIEW_FAILED`。

## Recommendation

`ROOT_CAUSE_FOUND: YES`

`DESIGN_GAP: NO`

generalized policy 可以从现有设计推出：`YES`。

建议批准新的 T006 remediation implementation attempt：`YES`，但仅在实施范围明确采用本文的
typed allowlist、unknown fail-closed、classification/rendering separation 和 invariant-driven test
matrix，并继续禁止 Wave 4 Integration / Wave 5 的前提下。
