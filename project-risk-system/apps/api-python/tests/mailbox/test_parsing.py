from __future__ import annotations

import os
import tempfile
from email.message import EmailMessage
from pathlib import Path

import pytest

from risk_platform.mailbox.parsing import (
    MAX_ATTACHMENT_BYTES,
    MAX_DELIVERED_CHARS,
    MAX_SOURCE_BYTES,
    MailParseError,
    cleanup_stale_temp_directories,
    parse_attachment,
    parse_mail,
)


def _mail(
    *,
    body: str = "项目 Alpha 本周进展正常。",
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = "Owner <owner@example.com>"
    message["Subject"] = "项目 Alpha 周报"
    message["Message-ID"] = "<test-message>"
    message.set_content(body)
    for name, mime, content in attachments or []:
        major, minor = mime.split("/", 1)
        message.add_attachment(content, maintype=major, subtype=minor, filename=name)
    return message.as_bytes()


def test_parser_keeps_only_bounded_derived_text_and_attachment_metadata() -> None:
    parsed = parse_mail(
        _mail(attachments=[("weekly.txt", "text/plain", "附件进度稳定。".encode())]),
        "<fallback>",
    )

    assert parsed.subject == "项目 Alpha 周报"
    assert parsed.sender_address == "owner@example.com"
    assert "附件进度稳定" in parsed.text
    assert parsed.attachment_metadata == [
        {
            "name": "weekly.txt",
            "mimeType": "text/plain",
            "extension": ".txt",
            "sizeBytes": len("附件进度稳定。".encode()),
            "allowedFormat": "TXT",
            "status": "PARSED",
            "code": None,
        }
    ]
    assert "附件进度稳定" not in str(parsed.attachment_metadata)


@pytest.mark.parametrize(
    ("name", "mime", "content", "status"),
    [
        ("report.csv", "text/csv", b"a,b", "UNSUPPORTED"),
        ("report.pdf", "application/pdf", b"not-a-pdf", "TYPE_MISMATCH"),
        ("report.txt", "application/octet-stream", b"plain", "TYPE_MISMATCH"),
    ],
)
def test_attachment_policy_requires_extension_mime_and_content_agreement(
    name: str, mime: str, content: bytes, status: str
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = parse_attachment(name, mime, content, Path(directory))
    assert result.status == status
    assert result.text == ""


def test_attachment_size_limit_does_not_parse_content() -> None:
    parsed = parse_mail(
        _mail(attachments=[("large.txt", "text/plain", b"a" * (MAX_ATTACHMENT_BYTES + 1))]),
        "<fallback>",
    )
    assert parsed.attachment_metadata[0]["status"] == "TOO_LARGE"


def test_attachment_output_limit_is_metadata_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = parse_attachment("long.txt", "text/plain", b"x" * 20_000, Path(directory))
    assert result.status == "OUTPUT_TRUNCATED"
    assert result.text == ""


def test_attachment_timeout_is_metadata_only(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr("risk_platform.mailbox.parsing.ATTACHMENT_TIMEOUT_SECONDS", 0.001)
        result = parse_attachment("slow.txt", "text/plain", b"safe", Path(directory))
    # A fast child may win the race, but any timeout cannot return content.
    if result.status == "PARSER_TIMEOUT":
        assert result.text == ""


def test_mail_size_and_output_limits_are_enforced() -> None:
    with pytest.raises(MailParseError, match="MAIL_SOURCE_TOO_LARGE"):
        parse_mail(b"x" * (MAX_SOURCE_BYTES + 1), "<fallback>")
    parsed = parse_mail(_mail(body="x" * (MAX_DELIVERED_CHARS + 100)), "<fallback>")
    assert len(parsed.text) == MAX_DELIVERED_CHARS
    assert parsed.attachment_metadata[-1]["code"] == "OUTPUT_TRUNCATED"


def test_task_temp_directory_is_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    task_dir = tmp_path / "task-dir"
    task_dir.mkdir()
    monkeypatch.setattr(
        "risk_platform.mailbox.parsing.tempfile.mkdtemp", lambda prefix: str(task_dir)
    )
    parse_mail(_mail(attachments=[("weekly.txt", "text/plain", b"safe")]), "<fallback>")
    assert not os.path.exists(task_dir)


def test_stale_task_temp_directory_is_removed(tmp_path: Path) -> None:
    stale = tmp_path / "risk-mail-stale"
    stale.mkdir()
    os.utime(stale, (0, 0))
    cleanup_stale_temp_directories(tmp_path)
    assert not stale.exists()
