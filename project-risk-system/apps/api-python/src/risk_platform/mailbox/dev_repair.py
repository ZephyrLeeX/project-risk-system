"""Explicit, narrowly-scoped development repair for stale mailbox terminal state."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from risk_platform.db import (
    create_database_engine,
    create_session_factory,
    database_url,
    dispose_database_engine,
    transaction,
)
from risk_platform.mailbox.models import (
    MailMessage,
    MailMessageStatus,
    MailSourceHandoff,
    MailStageStatus,
)
from risk_platform.model_types import JSONValue


async def repair_succeeded_analyzing_messages(
    sessions: async_sessionmaker[AsyncSession],
) -> int:
    """Repair only SUCCEEDED handoffs whose messages are still ANALYZING."""

    repaired = 0
    async with transaction(sessions) as session:
        rows = (
            await session.execute(
                select(MailSourceHandoff, MailMessage)
                .join(
                    MailMessage,
                    (MailMessage.mailboxConfigId == MailSourceHandoff.mailboxConfigId)
                    & (MailMessage.uidValidity == MailSourceHandoff.uidValidity)
                    & (MailMessage.imapUid == MailSourceHandoff.imapUid),
                )
                .where(
                    MailSourceHandoff.aiReviewStatus == MailStageStatus.SUCCEEDED,
                    MailMessage.status == MailMessageStatus.ANALYZING,
                )
                .with_for_update()
            )
        ).all()
        for _handoff, message in rows:
            trace = message.processingTrace if isinstance(message.processingTrace, list) else []
            if not any(
                isinstance(item, dict)
                and item.get("stage") == "AI_REVIEW"
                and item.get("status") == "COMPLETED"
                for item in trace
            ):
                message.processingTrace = cast(
                    "JSONValue",
                    [
                        *trace,
                        {
                            "stage": "AI_REVIEW",
                            "status": "COMPLETED",
                            "detail": "RISK_EXTRACTION_COMPLETED",
                            "occurredAt": datetime.now(UTC).isoformat(),
                        },
                    ],
                )
            message.status = MailMessageStatus.COMPLETED
            message.processedAt = message.processedAt or datetime.now(UTC)
            message.failureCode = message.failureSummary = None
            repaired += 1
    return repaired


async def _main() -> int:
    engine = create_database_engine(database_url())
    try:
        sessions = create_session_factory(engine)
        repaired = await repair_succeeded_analyzing_messages(sessions)
        print(f"repaired mailbox messages: {repaired}")
        return 0
    finally:
        await dispose_database_engine(engine)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-dev-repair",
        action="store_true",
        help="required acknowledgement for the one-time development repair",
    )
    args = parser.parse_args()
    if not args.confirm_dev_repair:
        parser.error("refusing to modify data without --confirm-dev-repair")
    raise SystemExit(asyncio.run(_main()))


__all__ = ["main", "repair_succeeded_analyzing_messages"]
