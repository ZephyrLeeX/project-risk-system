"""Bounded parsing for untrusted IMAP messages and their approved attachments."""

from __future__ import annotations

import html
import multiprocessing
import os
import re
import resource
import shutil
import signal
import socket
import tempfile
import time
import zipfile
from dataclasses import dataclass
from email import policy
from email.headerregistry import Address
from email.parser import BytesParser
from html.parser import HTMLParser
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Final
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from pypdf import PdfReader

MAX_SOURCE_BYTES: Final = 20 * 1024 * 1024
MAX_ATTACHMENTS: Final = 10
MAX_ATTACHMENT_BYTES: Final = 5 * 1024 * 1024
MAX_ATTACHMENT_TOTAL_BYTES: Final = 15 * 1024 * 1024
MAX_ATTACHMENT_CHARS: Final = 20_000
MAX_DELIVERED_CHARS: Final = 60_000
MAX_ZIP_ENTRIES: Final = 200
MAX_ZIP_MEMBER_BYTES: Final = 10 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES: Final = 25 * 1024 * 1024
MAX_ZIP_RATIO: Final = 20
MAX_PDF_PAGES: Final = 200
MIME_PARSE_TIMEOUT_SECONDS: Final = 5
ATTACHMENT_TIMEOUT_SECONDS: Final = 5
MAIL_PARSE_TIMEOUT_SECONDS: Final = 30
HELPER_CPU_SECONDS: Final = 3
HELPER_ADDRESS_SPACE_BYTES: Final = 256 * 1024 * 1024
FILENAME_LIMIT: Final = 255
SUMMARY_LIMIT: Final = 1_000
KEY_POINT_LIMIT: Final = 300
KEY_POINTS_LIMIT: Final = 5
EVIDENCE_LIMIT: Final = 500

_APPROVED: Final = {
    ".txt": ("text/plain", "TXT"),
    ".pdf": ("application/pdf", "PDF"),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "DOCX"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "XLSX"),
}


@dataclass(frozen=True, slots=True)
class AttachmentResult:
    name: str
    mime_type: str
    extension: str
    size_bytes: int
    allowed_format: str | None
    status: str
    code: str | None
    text: str = ""

    def metadata(self) -> dict[str, object]:
        return {
            "name": self.name,
            "mimeType": self.mime_type,
            "extension": self.extension,
            "sizeBytes": self.size_bytes,
            "allowedFormat": self.allowed_format,
            "status": self.status,
            "code": self.code,
        }


@dataclass(frozen=True, slots=True)
class ParsedMail:
    subject: str
    sender_name: str | None
    sender_address: str | None
    message_id: str
    sent_at: object | None
    summary: str
    key_points: list[str]
    evidence: list[str]
    attachment_metadata: list[dict[str, object]]
    text: str


class MailParseError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def clean_text(value: str) -> str:
    value = value.replace("\x00", " ")
    value = "".join(char if char >= " " or char in "\n\t" else " " for char in value)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", value)).strip()


def _safe_name(value: str | None) -> str:
    return clean_text(value or "unnamed")[:FILENAME_LIMIT] or "unnamed"


def _mime(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _extension(name: str) -> str:
    return Path(name).suffix.lower()


def _html_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return clean_text(html.unescape(" ".join(parser.parts)))


def _zip_preflight(path: Path, required: set[str]) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(path)
    infos = archive.infolist()
    names = [item.filename for item in infos]
    if archive.comment or len(infos) > MAX_ZIP_ENTRIES or len(set(names)) != len(names):
        archive.close()
        raise MailParseError("ZIP_LIMIT_EXCEEDED")
    total = 0
    for item in infos:
        unsafe_path = item.filename.startswith("/") or ".." in Path(item.filename).parts
        if item.flag_bits & 0x1 or unsafe_path:
            archive.close()
            raise MailParseError("ENCRYPTED" if item.flag_bits & 0x1 else "ZIP_LIMIT_EXCEEDED")
        if item.file_size > MAX_ZIP_MEMBER_BYTES:
            archive.close()
            raise MailParseError("ZIP_LIMIT_EXCEEDED")
        total += item.file_size
        ratio_exceeded = item.compress_size and item.file_size / item.compress_size > MAX_ZIP_RATIO
        if total > MAX_ZIP_TOTAL_BYTES or ratio_exceeded:
            archive.close()
            raise MailParseError("ZIP_LIMIT_EXCEEDED")
    if not required.issubset(names):
        archive.close()
        raise MailParseError("MALFORMED")
    return archive


def _xml_text(root: Element) -> str:
    return " ".join(item.text or "" for item in root.iter() if item.text)


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    with archive.open(name) as item:
        return item.read(MAX_ZIP_MEMBER_BYTES + 1)


def _parse_docx(path: Path) -> str:
    with _zip_preflight(path, {"word/document.xml"}) as archive:
        return _xml_text(ElementTree.fromstring(_read_member(archive, "word/document.xml")))


def _parse_xlsx(path: Path) -> str:
    with _zip_preflight(path, {"xl/workbook.xml"}) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(_read_member(archive, "xl/sharedStrings.xml"))
            shared = [_xml_text(item) for item in root]
        values: list[str] = []
        for name in archive.namelist():
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                root = ElementTree.fromstring(_read_member(archive, name))
                for cell in root.iter():
                    if cell.tag.endswith("}c"):
                        index = next((item.text for item in cell if item.tag.endswith("}v")), None)
                        is_shared = (
                            cell.attrib.get("t") == "s"
                            and index
                            and index.isdigit()
                            and int(index) < len(shared)
                        )
                        if is_shared:
                            values.append(shared[int(str(index))])
                        elif index:
                            values.append(index)
                    elif cell.tag.endswith("}is"):
                        values.append(_xml_text(cell))
        return "\n".join(values)


def _parse_pdf(path: Path) -> str:
    reader = PdfReader(path, strict=True)
    if len(reader.pages) > MAX_PDF_PAGES:
        raise MailParseError("PDF_PAGE_LIMIT_EXCEEDED")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _helper(path_name: str, extension: str, output: Connection) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (HELPER_CPU_SECONDS, HELPER_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (HELPER_ADDRESS_SPACE_BYTES, HELPER_ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    socket.socket = _blocked_socket  # type: ignore[assignment,misc]
    path = Path(path_name)
    try:
        if extension == ".txt":
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            if b"\x00" in raw:
                raise MailParseError("MALFORMED")
            text = raw.decode("utf-8", "strict")
        elif extension == ".pdf":
            text = _parse_pdf(path)
        elif extension == ".docx":
            text = _parse_docx(path)
        else:
            text = _parse_xlsx(path)
        cleaned = clean_text(text)
        if len(cleaned) >= MAX_ATTACHMENT_CHARS:
            output.send(("OUTPUT_TRUNCATED", ""))
        else:
            output.send(("OK", cleaned))
    except MailParseError as exc:
        output.send((exc.code, ""))
    except (UnicodeError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
        output.send(("MALFORMED", ""))
    except MemoryError:
        output.send(("PARSER_RESOURCE_LIMIT", ""))
    except Exception:
        output.send(("MALFORMED", ""))


def _content_matches(extension: str, content: bytes) -> bool:
    if extension == ".txt":
        try:
            content.removeprefix(b"\xef\xbb\xbf").decode("utf-8")
            return b"\x00" not in content
        except UnicodeDecodeError:
            return False
    if extension == ".pdf":
        return content.startswith(b"%PDF-")
    if extension in {".docx", ".xlsx"}:
        return content.startswith(b"PK\x03\x04")
    return False


def parse_attachment(
    name: str | None,
    declared_mime: str | None,
    content: bytes,
    temp_dir: Path,
    timeout_seconds: float = ATTACHMENT_TIMEOUT_SECONDS,
) -> AttachmentResult:
    safe_name = _safe_name(name)
    extension = _extension(safe_name)
    mime_type = _mime(declared_mime)
    approved = _APPROVED.get(extension)

    def result(
        allowed_format: str | None, status: str, code: str | None, text: str = ""
    ) -> AttachmentResult:
        return AttachmentResult(
            safe_name, mime_type, extension, len(content), allowed_format, status, code, text
        )

    if len(content) > MAX_ATTACHMENT_BYTES:
        return result(_APPROVED.get(extension, ("", None))[1], "TOO_LARGE", "TOO_LARGE")

    if approved is None:
        return result(None, "UNSUPPORTED", "UNSUPPORTED")
    expected_mime, allowed_format = approved
    if mime_type != expected_mime or not _content_matches(extension, content):
        return result(allowed_format, "TYPE_MISMATCH", "TYPE_MISMATCH")
    task_file = temp_dir / os.urandom(16).hex()
    task_file.write_bytes(content)
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    process = multiprocessing.Process(
        target=_helper, args=(str(task_file), extension, child_conn), daemon=True
    )
    process.start()
    process.join(timeout_seconds)
    try:
        if process.is_alive():
            process.terminate()
            process.join()
            return result(allowed_format, "PARSER_TIMEOUT", "PARSER_TIMEOUT")
        child_conn.close()
        try:
            code, text = parent_conn.recv() if parent_conn.poll() else ("MALFORMED", "")
        except (EOFError, OSError):
            code, text = ("PARSER_RESOURCE_LIMIT" if process.exitcode else "MALFORMED"), ""
        status = "PARSED" if code == "OK" else code
        return result(allowed_format, status, None if code == "OK" else code, text)
    finally:
        child_conn.close()
        parent_conn.close()
        task_file.unlink(missing_ok=True)


def parse_mail(source: bytes, fallback_message_id: str) -> ParsedMail:
    if len(source) > MAX_SOURCE_BYTES:
        raise MailParseError("MAIL_SOURCE_TOO_LARGE")
    started = time.monotonic()
    previous_handler = signal.signal(signal.SIGALRM, _mime_timeout)
    signal.setitimer(signal.ITIMER_REAL, MIME_PARSE_TIMEOUT_SECONDS)
    try:
        root = BytesParser(policy=policy.default).parsebytes(source)
    except TimeoutError:
        raise MailParseError("MIME_PARSE_TIMEOUT") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    cleanup_stale_temp_directories()
    temp_dir = Path(tempfile.mkdtemp(prefix="risk-mail-"))
    os.chmod(temp_dir, 0o700)
    try:
        body = ""
        html_body = ""
        attachments: list[AttachmentResult] = []
        total_size = 0
        deadline = started + MAIL_PARSE_TIMEOUT_SECONDS
        for part in root.walk():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MailParseError("MIME_PARSE_TIMEOUT")
            if part.is_multipart():
                continue
            disposition = part.get_content_disposition()
            filename = part.get_filename()
            if disposition == "attachment" or filename:
                if len(attachments) >= MAX_ATTACHMENTS:
                    attachments.append(_too_many(filename, part.get_content_type()))
                    continue
                raw = bytes(part.get_payload(decode=True) or b"")
                total_size += len(raw)
                if len(raw) > MAX_ATTACHMENT_BYTES or total_size > MAX_ATTACHMENT_TOTAL_BYTES:
                    attachments.append(_too_large(filename, part.get_content_type(), raw))
                    continue
                attachments.append(
                    parse_attachment(
                        filename,
                        part.get_content_type(),
                        raw,
                        temp_dir,
                        timeout_seconds=min(ATTACHMENT_TIMEOUT_SECONDS, remaining),
                    )
                )
            elif part.get_content_type() == "text/plain":
                body = clean_text(part.get_content())
            elif part.get_content_type() == "text/html":
                html_body = _html_text(part.get_content())
        segments = [body or html_body, *(item.text for item in attachments if item.text)]
        text = clean_text("\n\n".join(segments))
        truncated = len(text) > MAX_DELIVERED_CHARS
        text = text[:MAX_DELIVERED_CHARS]
        metadata = [item.metadata() for item in attachments]
        if truncated:
            metadata.append(
                {
                    "name": "",
                    "mimeType": "",
                    "extension": "",
                    "sizeBytes": 0,
                    "allowedFormat": None,
                    "status": "OUTPUT_TRUNCATED",
                    "code": "OUTPUT_TRUNCATED",
                }
            )
        sender = root["From"].addresses[0] if root["From"] and root["From"].addresses else None
        assert sender is None or isinstance(sender, Address)
        points = [
            value[:KEY_POINT_LIMIT]
            for value in re.split(r"(?<=[。\uFF01\uFF1F!?])|\n+", text)
            if len(value.strip()) >= 8
        ][:KEY_POINTS_LIMIT]
        if time.monotonic() > deadline:
            raise MailParseError("MIME_PARSE_TIMEOUT")
        return ParsedMail(
            subject=clean_text(str(root.get("Subject") or "(no subject)"))[:500],
            sender_name=(
                clean_text(sender.display_name)[:255] if sender and sender.display_name else None
            ),
            sender_address=sender.addr_spec.lower()[:255] if sender else None,
            message_id=clean_text(str(root.get("Message-ID") or fallback_message_id))[:500],
            sent_at=root["Date"].datetime if root["Date"] else None,
            summary=text[:SUMMARY_LIMIT],
            key_points=points,
            evidence=[text[:EVIDENCE_LIMIT]] if text else [],
            attachment_metadata=metadata,
            text=text,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


__all__ = ["AttachmentResult", "MailParseError", "ParsedMail", "parse_attachment", "parse_mail"]


def _too_large(name: str | None, declared_mime: str | None, content: bytes) -> AttachmentResult:
    safe_name = _safe_name(name)
    extension = _extension(safe_name)
    allowed = _APPROVED.get(extension, ("", None))[1]
    return AttachmentResult(
        safe_name, _mime(declared_mime), extension, len(content), allowed, "TOO_LARGE", "TOO_LARGE"
    )


def _too_many(name: str | None, declared_mime: str | None) -> AttachmentResult:
    safe_name = _safe_name(name)
    extension = _extension(safe_name)
    allowed = _APPROVED.get(extension, ("", None))[1]
    return AttachmentResult(
        safe_name, _mime(declared_mime), extension, 0, allowed, "TOO_LARGE", "TOO_LARGE"
    )


def _blocked_socket(*args: object, **kwargs: object) -> object:
    del args, kwargs
    raise OSError("network disabled")


def _mime_timeout(signum: int, frame: object) -> None:
    del signum, frame
    raise TimeoutError


def cleanup_stale_temp_directories(root: Path | None = None) -> None:
    """Worker startup/reconciliation may call this; parse calls it defensively too."""
    base = root or Path(tempfile.gettempdir())
    deadline = time.time() - 3600
    for path in base.glob("risk-mail-*"):
        try:
            if path.is_dir() and path.stat().st_mtime < deadline:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue
