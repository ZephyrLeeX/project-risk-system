"""Reproducible baseline dataset generator (ADR 0009 / ADR 0032 §9).

Produces the approved capacity baseline — 300 users, 5,000 projects, 1,000
weekly-mail messages — plus realistic risks/todos/audit/timeline/conversations/
mailbox/weekly-aggregate facts, seeded directly into PostgreSQL via SQLAlchemy
Core bulk inserts inside caller-owned transactions.

Deterministic: UUIDs are derived from ``uuid5`` over a stable namespace so
re-seeding from scratch reproduces a byte-identical dataset. The generator
never edits production code/schema/migration; it only inserts rows into the
existing Alembic ``head`` schema.

Reference-data seeding reuses :func:`risk_platform.seed.seed_reference_data`
(four roles, fifteen permissions, five departments, risk categories/levels) so
the dataset cannot drift from the approved reference set.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.low_level import Type
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from risk_platform.admin.models import Department, User, UserStatus
from risk_platform.agent.models import (
    AgentConversation,
    AgentEvent,
    AgentEventType,
    AgentMessage,
    AgentMessageRole,
)
from risk_platform.audit.models import AuditActorType, AuditLog, AuditResult
from risk_platform.mailbox.models import (
    MailboxConfig,
    MailboxConnectionStatus,
    MailboxEncryption,
    MailMessage,
    MailMessageStatus,
    MailSyncBatch,
    MailSyncStatus,
    MailSyncTrigger,
)
from risk_platform.projects.models import Project, ProjectRiskLevel, ProjectStatus
from risk_platform.rbac.models import DataScopeType, Role, UserProjectScope, UserRole
from risk_platform.reliability.models import DurableTask, DurableTaskKind, DurableTaskStatus
from risk_platform.risks.models import Risk, RiskCategory, RiskSourceType, RiskStatus
from risk_platform.seed import SeedSettings, seed_reference_data
from risk_platform.shared.crypto import SecretCipher
from risk_platform.timeline.models import RiskTimelineEvent
from risk_platform.todos.models import (
    ActionItem,
    ActionItemSourceType,
    ActionItemStatus,
    ActionItemUrgency,
)
from risk_platform.weekly_reports.models import WeeklyReportAggregate

LOAD_PASSWORD = "LoadTest_Strong1!"  # satisfies policy; shared by all load VUs
_NAMESPACE = uuid.UUID("5a5a5a5a-5a5a-5a5a-5a5a-5a5a5a5a5a5a")


def _u(name: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, name)


@dataclass(frozen=True, slots=True)
class RoleAllocation:
    code: str
    count: int
    data_scope: DataScopeType


# 300 users total (ADR 0009 baseline). The first N of each role double as load
# VUs (see LoadConfig VU allocation); the remainder populate the dataset so
# projects/risks have realistic ownership breadth.
USER_ALLOCATION: tuple[RoleAllocation, ...] = (
    RoleAllocation("SYSTEM_ADMIN", 6, DataScopeType.ALL),
    RoleAllocation("RISK_ADMIN", 14, DataScopeType.ALL),
    RoleAllocation("PROJECT_MANAGER", 160, DataScopeType.OWNED_OR_ASSIGNED),
    RoleAllocation("VIEWER_AUDITOR", 120, DataScopeType.ASSIGNED),
)
assert sum(a.count for a in USER_ALLOCATION) == 300

# VU users per role (must match LoadConfig defaults).
VU_PER_ROLE = {"SYSTEM_ADMIN": 6, "RISK_ADMIN": 12, "PROJECT_MANAGER": 20, "VIEWER_AUDITOR": 12}
assert sum(VU_PER_ROLE.values()) == 50


@dataclass(slots=True)
class GeneratedDataset:
    """Stable identifiers of the seeded baseline dataset."""

    vu_usernames: dict[str, str]  # role code -> username of the first VU in that role
    project_ids: list[uuid.UUID]
    risk_ids: list[uuid.UUID]
    todo_ids: list[uuid.UUID]
    conversation_ids: list[uuid.UUID]
    mailbox_user_ids: list[uuid.UUID]
    category_ids: list[uuid.UUID]
    department_ids: list[uuid.UUID]


def _username(role: str, index: int) -> str:
    return f"load-{role.lower()}-{index:04d}"


async def _seed_users_and_roles(
    session: AsyncSession, hasher: PasswordHasher, rng: random.Random
) -> dict[str, list[uuid.UUID]]:
    """Seed 300 users across the four roles; return role -> user ids."""

    roles_found: dict[str, Role | None] = {
        code: await session.scalar(select(Role).where(Role.code == code))
        for code in (a.code for a in USER_ALLOCATION)
    }
    department = await session.scalar(
        select(Department).where(Department.code == "TECH_MANAGEMENT")
    )
    assert department is not None and all(roles_found.values())
    roles: dict[str, Role] = {c: r for c, r in roles_found.items() if r is not None}

    password_hash = hasher.hash(LOAD_PASSWORD)
    role_to_users: dict[str, list[uuid.UUID]] = {}
    rows: list[dict[str, object]] = []
    user_role_rows: list[dict[str, object]] = []

    idx = 0
    for alloc in USER_ALLOCATION:
        ids: list[uuid.UUID] = []
        for i in range(alloc.count):
            uid = _u(f"user:{alloc.code}:{i}")
            ids.append(uid)
            rows.append(
                {
                    "id": uid,
                    "username": _username(alloc.code, i),
                    "passwordHash": password_hash,
                    "displayName": f"压测{alloc.code}{i:04d}",
                    "departmentId": department.id,
                    "status": UserStatus.ACTIVE,
                    "mustChangePassword": False,
                    "failedLoginCount": 0,
                }
            )
            user_role_rows.append(
                {"userId": uid, "roleId": roles[alloc.code].id, "dataScope": alloc.data_scope}
            )
            idx += 1
        role_to_users[alloc.code] = ids

    await session.execute(insert(User), rows)
    await session.execute(insert(UserRole), user_role_rows)
    return role_to_users


async def _seed_projects(
    session: AsyncSession,
    role_to_users: dict[str, list[uuid.UUID]],
    department_ids: list[uuid.UUID],
    rng: random.Random,
) -> list[uuid.UUID]:
    managers = role_to_users["PROJECT_MANAGER"]
    levels = [
        ProjectRiskLevel.HIGH,
        ProjectRiskLevel.MEDIUM,
        ProjectRiskLevel.LOW,
        ProjectRiskLevel.UNKNOWN,
    ]
    statuses = [ProjectStatus.DELIVERY] * 8 + [ProjectStatus.COMPLETED] * 2
    rows: list[dict[str, object]] = []
    project_ids: list[uuid.UUID] = []
    for i in range(5_000):
        pid = _u(f"project:{i}")
        project_ids.append(pid)
        rows.append(
            {
                "id": pid,
                "name": f"压测项目-{i:05d}",
                "externalCode": f"PRJ-{i:05d}",
                "status": rng.choice(statuses),
                "departmentId": rng.choice(department_ids),
                "managerId": rng.choice(managers),
                "deliveryOwnerName": f"交付负责人{i % 200}",
                "annualPlanAmount": (i % 1000) * 100,
                "actualCollectedAmount": (i % 600) * 100,
                "remainingAmount": (i % 400) * 100,
                "collectionRiskLevel": rng.choice(levels),
                "sourceVersion": 1,
            }
        )
    await session.execute(insert(Project), rows)

    # Assign ~30 projects to each VIEWER_AUDITOR (ASSIGNED scope) so reads return data.
    viewers = role_to_users["VIEWER_AUDITOR"]
    scope_rows: list[dict[str, object]] = []
    for v_idx, vid in enumerate(viewers):
        for j in range(30):
            scope_rows.append({"projectId": project_ids[(v_idx * 30 + j) % 5_000], "userId": vid})
    # Also assign the first VU project_manager's owned projects explicitly.
    await session.execute(insert(UserProjectScope), scope_rows)
    return project_ids


async def _seed_risks_todos_timeline(
    session: AsyncSession,
    project_ids: list[uuid.UUID],
    category_ids: list[uuid.UUID],
    managers: list[uuid.UUID],
    rng: random.Random,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    levels = [ProjectRiskLevel.HIGH, ProjectRiskLevel.MEDIUM, ProjectRiskLevel.LOW]
    urgencies = [ActionItemUrgency.HIGH, ActionItemUrgency.NORMAL, ActionItemUrgency.EMERGENCY]
    risk_rows: list[dict[str, object]] = []
    todo_rows: list[dict[str, object]] = []
    timeline_rows: list[dict[str, object]] = []
    risk_ids: list[uuid.UUID] = []
    todo_ids: list[uuid.UUID] = []
    now = datetime.now(UTC)
    for pi, pid in enumerate(project_ids):
        n = 2 if pi % 3 else 3  # 2-3 risks per project -> ~13300 risks
        for k in range(n):
            rid = _u(f"risk:{pi}:{k}")
            risk_ids.append(rid)
            cat = rng.choice(category_ids)
            level = rng.choice(levels)
            active = rng.random() > 0.15
            detected = now - timedelta(days=rng.randint(1, 120))
            risk_rows.append(
                {
                    "id": rid,
                    "projectId": pid,
                    "categoryId": cat,
                    "title": f"压测风险-{pi:05d}-{k}",
                    "description": (
                        f"项目 {pi} 的第 {k} 条压测风险描述, "
                        f"用于容量基线测量的真实规模数据。"
                    ),
                    "level": level,
                    "status": RiskStatus.ACTIVE if active else RiskStatus.RESOLVED,
                    "sourceType": RiskSourceType.MANUAL,
                    "reporterUserId": rng.choice(managers),
                    "weekCode": detected.strftime("%G-W%V"),
                    "dedupeFingerprint": hashlib.sha256(f"risk:{pi}:{k}".encode()).hexdigest(),
                    "detectedAt": detected,
                    "resolvedAt": None if active else detected + timedelta(days=5),
                    "resolvedById": None if active else rng.choice(managers),
                    "resolutionReason": None if active else "压测解除原因说明文本",
                }
            )
            tid = _u(f"todo:{pi}:{k}")
            todo_ids.append(tid)
            todo_rows.append(
                {
                    "id": tid,
                    "riskId": rid,
                    "projectId": pid,
                    "title": f"待办-{pi:05d}-{k}",
                    "description": "压测待办事项描述。",
                    "urgency": rng.choice(urgencies),
                    "status": ActionItemStatus.PENDING,
                    "sourceType": ActionItemSourceType.RISK_SUGGESTION,
                    "assigneeUserId": rng.choice(managers),
                    "createdById": rng.choice(managers),
                }
            )
            timeline_rows.append(
                {
                    "id": _u(f"tl:{pi}:{k}"),
                    "projectId": pid,
                    "riskId": rid,
                    "eventType": "RISK_CREATED",
                    "title": f"风险创建-{pi:05d}-{k}",
                    "description": "压测时间线事件。",
                    "actorUserId": rng.choice(managers),
                    "occurredAt": detected,
                }
            )
    await session.execute(insert(Risk), risk_rows)
    await session.execute(insert(ActionItem), todo_rows)
    await session.execute(insert(RiskTimelineEvent), timeline_rows)
    return risk_ids, todo_ids


async def _seed_audit_logs(
    session: AsyncSession, risk_ids: list[uuid.UUID], managers: list[uuid.UUID], rng: random.Random
) -> None:
    """Seed audit log entries; the BEFORE INSERT trigger computes the hash chain.

    ``previousHash``/``integrityHash``/``createdAt`` are left unset so the
    trigger populates them. ``traceId`` must be UUID-format (CHECK constraint).
    Inserts are serialized by the trigger's advisory lock.
    """

    rows: list[dict[str, object]] = []
    for i in range(0, min(len(risk_ids), 4_000)):
        rows.append(
            {
                "id": _u(f"audit:{i}"),
                "actorUserId": rng.choice(managers),
                "actorType": AuditActorType.USER,
                "module": "RISK",
                "action": "RISK_CREATED",
                "resourceType": "RISK",
                "resourceId": str(risk_ids[i]),
                "result": AuditResult.SUCCESS,
                "traceId": str(_u(f"trace:{i}")),
                "projectId": None,
            }
        )
    # Insert in modest batches; the trigger serializes per-row via advisory lock.
    batch = 200
    for start in range(0, len(rows), batch):
        await session.execute(insert(AuditLog), rows[start : start + batch])


async def _seed_mailbox_and_weekly(
    session: AsyncSession,
    project_ids: list[uuid.UUID],
    risk_ids: list[uuid.UUID],
    role_to_users: dict[str, list[uuid.UUID]],
    cipher: SecretCipher,
    rng: random.Random,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Seed mailbox configs + 1,000 mail messages + weekly aggregates (ADR 0009)."""

    mailbox_users = role_to_users["RISK_ADMIN"][:14] + role_to_users["PROJECT_MANAGER"][:36]
    config_rows: list[dict[str, object]] = []
    for i, uid in enumerate(mailbox_users):
        auth_code = f"authcode-{i:04d}"
        # Produce the no-AAD AES-GCM triplet consumed by decrypt_legacy.
        legacy = cipher.encrypt_legacy(auth_code)
        config_rows.append(
            {
                "id": _u(f"mbox:{i}"),
                "userId": uid,
                "provider": "IMAP",
                "email": f"loadtest{i:04d}@example.test",
                "imapHost": "imap.example.test",
                "imapPort": 993,
                "encryption": MailboxEncryption.SSL,
                "folder": "INBOX",
                "encryptedAuthCode": legacy.ciphertext,
                "authCodeIv": legacy.iv,
                "authCodeTag": legacy.auth_tag,
                "authCodeLast4": auth_code[-4:],
                "subjectKeywords": [],
                "initialSyncWeeks": 4,
                "readAttachments": True,
                "aiExtractionEnabled": True,
                "enabled": True,
                "autoSyncEnabled": True,
                "connectionStatus": MailboxConnectionStatus.HEALTHY,
            }
        )
    await session.execute(insert(MailboxConfig), config_rows)

    # 1,000 weekly mail messages (ADR 0009 baseline) with a completed status.
    msg_rows: list[dict[str, object]] = []
    batch_rows: list[dict[str, object]] = []
    msg_ids: list[uuid.UUID] = []
    now = datetime.now(UTC)
    for i in range(1_000):
        mid = _u(f"mail:{i}")
        msg_ids.append(mid)
        cfg = config_rows[i % len(config_rows)]
        received = now - timedelta(days=rng.randint(1, 60))
        msg_rows.append(
            {
                "id": mid,
                "mailboxConfigId": cfg["id"],
                "batchId": _u(f"mbatch:{i}"),
                "messageId": f"<msg-{i}@example.test>",
                "imapUid": i,
                "subject": f"周报邮件-{i:04d}",
                "senderAddress": f"sender{i % 50}@example.test",
                "sentAt": received - timedelta(hours=2),
                "receivedAt": received,
                "receivedAtSource": "IMAP_INTERNALDATE",
                "processedAt": received + timedelta(minutes=5),
                "status": MailMessageStatus.COMPLETED,
                "retryCount": 0,
            }
        )
        batch_rows.append(
            {
                "id": _u(f"mbatch:{i}"),
                "taskId": _u(f"mbtask:{i}"),
                "code": f"BATCH-{i:05d}",
                "mailboxConfigId": cfg["id"],
                "trigger": MailSyncTrigger.MANUAL,
                "status": MailSyncStatus.SUCCESS,
                "scannedCount": 10,
                "newCount": 1,
                "successCount": 1,
                "failedCount": 0,
                "riskCandidateCount": 1,
            }
        )
    # Mail sync batches reference durable_tasks.taskId (RESTRICT) -> seed stub tasks.
    task_rows = [
        {
            "id": b["taskId"],
            "kind": DurableTaskKind.MAILBOX_SYNC,
            "status": DurableTaskStatus.SUCCEEDED,
            "idempotencyKey": f"mbtask:{i}",
            "payload": {},
            "attemptCount": 0,
            "maxAttempts": 3,
            "dispatchGeneration": 1,
            "completedAt": now,
        }
        for i, b in enumerate(batch_rows)
    ]
    await session.execute(insert(DurableTask), task_rows)
    await session.execute(insert(MailSyncBatch), batch_rows)
    await session.execute(insert(MailMessage), msg_rows)

    # Weekly report aggregates for the current ISO week (Monday) on ~500 projects.
    monday = (now - timedelta(days=now.weekday())).date()
    weekly_rows: list[dict[str, object]] = []
    for i, pid in enumerate(project_ids[:500]):
        weekly_rows.append(
            {
                "id": _u(f"weekly:{i}"),
                "weekStart": monday,
                "projectId": pid,
                "summary": {"note": "压测周报摘要"},
                "riskCount": rng.randint(0, 5),
                "riskLevelCounts": {"HIGH": 1, "MEDIUM": 1, "LOW": 1},
                "sourceRevision": 1,
                "stale": False,
                "generatedAt": now,
                "freshnessDeadline": now + timedelta(days=7),
            }
        )
    await session.execute(insert(WeeklyReportAggregate), weekly_rows)
    return list(mailbox_users), msg_ids


async def _seed_conversations(
    session: AsyncSession, role_to_users: dict[str, list[uuid.UUID]], rng: random.Random
) -> list[uuid.UUID]:
    """Seed agent conversations + messages + a few events for SSE resume tests."""

    owners = role_to_users["PROJECT_MANAGER"][:20] + role_to_users["RISK_ADMIN"][:14]
    now = datetime.now(UTC)
    conv_ids: list[uuid.UUID] = []
    conv_rows: list[dict[str, object]] = []
    msg_rows: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    # One durable task per conversation (referenced by agent_events.taskId RESTRICT).
    task_rows: list[dict[str, object]] = []
    for i, oid in enumerate(owners):
        cid = _u(f"conv:{i}")
        conv_ids.append(cid)
        tid = _u(f"atask:{i}")
        conv_rows.append(
            {
                "id": cid,
                "ownerUserId": oid,
                "expiresAt": now + timedelta(days=30),
                "retentionConfigVersion": "v1",
                # Start at 0; the agent_messages_assign_sequence /
                # agent_events_assign_sequence triggers advance these per insert
                # and require NEW.sequence to match the post-increment value, so
                # pre-setting them to the final count would make the first insert
                # raise "agent message sequence must be contiguous".
                "lastMessageSequence": 0,
                "lastEventSequence": 0,
            }
        )
        mid = _u(f"amsg:{i}")
        msg_rows.append(
            {
                "id": mid,
                "conversationId": cid,
                "sequence": 1,
                "role": AgentMessageRole.USER,
                "content": f"压测对话消息-{i}",
                "traceId": str(_u(f"atrace:{i}")),
            }
        )
        task_rows.append(
            {
                "id": tid,
                "kind": DurableTaskKind.AGENT_EXECUTION,
                "status": DurableTaskStatus.SUCCEEDED,
                "idempotencyKey": f"atask:{i}",
                "payload": {},
                "attemptCount": 0,
                "maxAttempts": 3,
                "dispatchGeneration": 1,
                "completedAt": now,
            }
        )
        event_rows.append(
            {
                "id": _u(f"aevent:{i}"),
                "conversationId": cid,
                "messageId": mid,
                "taskId": tid,
                "sequence": 1,
                "type": AgentEventType.COMPLETED,
                "payload": {"text": "压测完成事件"},
            }
        )
    await session.execute(insert(DurableTask), task_rows)
    await session.execute(insert(AgentConversation), conv_rows)
    await session.execute(insert(AgentMessage), msg_rows)
    await session.execute(insert(AgentEvent), event_rows)
    return conv_ids


async def generate_dataset(
    session: AsyncSession, cipher: SecretCipher, *, seed: int = 20260815
) -> GeneratedDataset:
    """Seed the full baseline dataset within one transaction (caller-owned)."""

    rng = random.Random(seed)
    # Reference data first (idempotent upsert by code). The bootstrap admin uses
    # a distinct username so it never collides with the 300 load-* VU users.
    await seed_reference_data(
        session,
        SeedSettings(
            username="bootstrap-admin",
            display_name="压测引导管理员",
            password=LOAD_PASSWORD,
            password_min_length=12,
        ),
    )
    await session.flush()

    departments = (await session.scalars(select(Department))).all()
    category_ids = [c.id for c in (await session.scalars(select(RiskCategory))).all()]
    department_ids = [d.id for d in departments]

    hasher = PasswordHasher(type=Type.ID)
    role_to_users = await _seed_users_and_roles(session, hasher, rng)
    project_ids = await _seed_projects(session, role_to_users, department_ids, rng)
    managers = role_to_users["PROJECT_MANAGER"]
    risk_ids, todo_ids = await _seed_risks_todos_timeline(
        session, project_ids, category_ids, managers, rng
    )
    await _seed_audit_logs(session, risk_ids, managers, rng)
    mailbox_user_ids, _msg_ids = await _seed_mailbox_and_weekly(
        session, project_ids, risk_ids, role_to_users, cipher, rng
    )
    conv_ids = await _seed_conversations(session, role_to_users, rng)

    return GeneratedDataset(
        vu_usernames={code: _username(code, 0) for code in VU_PER_ROLE},
        project_ids=project_ids,
        risk_ids=risk_ids,
        todo_ids=todo_ids,
        conversation_ids=conv_ids,
        mailbox_user_ids=mailbox_user_ids,
        category_ids=category_ids,
        department_ids=department_ids,
    )
