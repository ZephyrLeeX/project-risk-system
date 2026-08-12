# T025 — Parse mail safely and match projects
- **Task ID:** T025
- **Title:** Parse mail safely and match projects
- **Status:** DESIGN_GAP
- **Objective:** Convert fetched mail into minimal retained summaries/metadata and deterministic standard-name/alias matches.
- **Design baseline:** Design §§6,7.
- **Authoritative source references:** `project-risk-system/apps/api/src/mailbox/mail-content-parser.service.ts`, `mail-project-matcher.service.ts` and tests; T013 aliases; mail contracts; ADR 0022.
- **Relevant ADR IDs:** 0007, 0014, 0015, 0022.
- **Dependencies:** T007, T013, T024 and ADR 0022.
- **Scope:** MIME/body sanitization, bounded supported attachments, temp lifecycle, key points/metadata/evidence extraction and project matching.
- **Explicit out-of-scope:** AI risk extraction, fuzzy/manual matching not approved by current target behavior, full-content retention.
- **Expected read set:** Named parser/matcher/schema/config sources.
- **Expected write set:** Python mailbox parsing/matching modules/tasks/tests.
- **Contracts/invariants:** Consume durable handoff by `(mailbox_config_id, uid_validity, imap_uid)` and re-fetch source from IMAP; no full body/attachment in PostgreSQL/Redis/Celery payload/log/audit or long-term storage; stage result is `SUCCEEDED`, `RETRYABLE_FAILURE` or structured `PERMANENT_FAILURE`; standard names and active aliases; untrusted input bounded.
- **Acceptance criteria:** MIME/attachment bomb/timeout/temp-cleanup and exact/alias fixtures pass.
- **Validation:** Parser tests plus worker integration.
- **Required deliverables:** Sanitizer/parsers/matcher/tests.
- **Stop conditions:** ADR 0022 source identity/refetch contract cannot be satisfied or a required file type lacks an approved safe parser policy.
- **Known integration risks:** Office/PDF parser resource limits and false matches.

## DESIGN_GAP

批准设计未定义附件允许类型、安全解析器以及 timeout / resource limits。不得以 legacy `.txt`、`.docx`、`.pdf`、`.xlsx` 行为自行补充该安全策略；在新的批准设计前，T025 保持 `DESIGN_GAP`。
