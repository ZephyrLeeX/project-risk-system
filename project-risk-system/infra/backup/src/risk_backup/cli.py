"""Operator CLI for one-shot encrypted backup/restore (ADR 0031 §10, §12).

Emits metadata-only JSON log records to stderr: backupId, backupType, time, KEK
key version (never the key), component names/sizes/sha256, status, measured RTO,
error codes. Never logs keys, DEKs, plaintext, file/dump contents or credentials.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from risk_backup.backup import BackupOutcome, BackupRequest, run_backup
from risk_backup.errors import BackupError
from risk_backup.keys import BackupKeyRing, load_backup_key_ring
from risk_backup.manifest import BackupType
from risk_backup.pgdump import PgConnection, PgDumper
from risk_backup.quiesce import ComposeQuiescer, NoopQuiescer, Quiescer
from risk_backup.restore import RestoreRequest, run_restore


def log_record(record: dict[str, object]) -> None:
    """Metadata-only JSON log to stderr (no keys/plaintext ever)."""

    sys.stderr.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stderr.flush()


def _parse_kek_files(items: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            raise BackupError("INVALID_KEK_FILE_ARG")
        version, path = item.split("=", 1)
        result[version] = Path(path)
    return result


def _split_dsn(dsn: str) -> dict[str, str | None]:
    parts = urlsplit(dsn)
    return {
        "user": unquote(parts.username) if parts.username else None,
        "password": unquote(parts.password) if parts.password else None,
        "host": parts.hostname,
        "port": str(parts.port) if parts.port else None,
        "database": parts.path.lstrip("/") if parts.path else None,
    }


def _build_pg_dumper(args: argparse.Namespace) -> PgDumper:
    parsed = _split_dsn(args.dsn)
    user = args.pg_user or parsed["user"] or "project_risk"
    database = args.pg_db or parsed["database"] or "project_risk"
    runner = args.pg_runner.split() if args.pg_runner else []
    if args.pg_socket_dir:
        conn = PgConnection(
            username=user, database=database, socket_dir=args.pg_socket_dir
        )
    else:
        conn = PgConnection(
            username=user,
            database=database,
            host=args.pg_host or parsed["host"],
            port=int(args.pg_port) if args.pg_port else (
                int(parsed["port"]) if parsed["port"] else None
            ),
            password=args.pg_password or parsed["password"],
        )
    return PgDumper(connection=conn, runner=runner)


def _build_quiescer(args: argparse.Namespace) -> Quiescer:
    if args.quiesce == "none":
        return NoopQuiescer()
    if args.quiesce == "compose":
        if not args.compose_file:
            raise BackupError("COMPOSE_FILE_REQUIRED")
        return ComposeQuiescer(
            compose_file=args.compose_file,
            project_dir=args.project_dir,
        )
    raise BackupError("UNKNOWN_QUIESCE_MODE")


def _load_key_ring(args: argparse.Namespace) -> BackupKeyRing:
    kek_files = _parse_kek_files(args.kek_file)
    return load_backup_key_ring(args.kek_version, kek_files)


def cmd_backup(args: argparse.Namespace) -> int:
    key_ring = _load_key_ring(args)
    pg_dumper = _build_pg_dumper(args)
    quiescer = _build_quiescer(args)
    request = BackupRequest(
        backup_type=BackupType(args.type),
        dsn=args.dsn,
        pg_dumper=pg_dumper,
        storage_root=Path(args.storage_root),
        output_path=Path(args.output),
        backup_key_ring=key_ring,
        quiescer=quiescer,
        temp_dir=Path(args.temp_dir),
        created_by=args.created_by,
        trace_id=args.trace_id,
    )
    start = time.monotonic()
    outcome = run_backup(request)
    rto_seconds = round(time.monotonic() - start, 3)
    _log_backup_outcome(outcome, rto_seconds)
    if outcome.usable and outcome.error is None and outcome.unquiesce_warning is None:
        return 0
    return 1


def _log_backup_outcome(outcome: BackupOutcome, rto_seconds: float) -> None:
    record: dict[str, object] = {
        "event": "backup",
        "status": outcome.status.value,
        "rtoSeconds": rto_seconds,
    }
    if outcome.manifest is not None:
        m = outcome.manifest
        record.update(
            {
                "backupId": m.backupId,
                "backupType": m.backupType.value,
                "createdAt": m.createdAt,
                "kekKeyVersion": m.encryption.kekKeyVersion,
                "pg": {
                    "file": m.pg.file,
                    "sizeBytes": m.pg.sizeBytes,
                    "sha256": m.pg.sha256,
                    "alembicHead": m.pg.alembicHead,
                },
                "files": {
                    "file": m.files.file,
                    "sizeBytes": m.files.sizeBytes,
                    "sha256": m.files.sha256,
                    "entryCount": m.files.entryCount,
                },
            }
        )
    if outcome.error is not None:
        record["errorCode"] = outcome.error.code
    if outcome.cleanup_warning is not None:
        record["cleanupWarning"] = outcome.cleanup_warning.code
    if outcome.unquiesce_warning is not None:
        record["unquiesceWarning"] = outcome.unquiesce_warning.code
    log_record(record)


def cmd_restore(args: argparse.Namespace) -> int:
    key_ring = _load_key_ring(args)
    pg_dumper = _build_pg_dumper(args)
    request = RestoreRequest(
        artifact_path=Path(args.artifact),
        target_dsn=args.target_dsn,
        pg_dumper=pg_dumper,
        target_storage_root=Path(args.target_storage_root),
        backup_key_ring=key_ring,
        plaintext_dir=Path(args.temp_dir),
    )
    start = time.monotonic()
    outcome = run_restore(request)
    rto_seconds = round(time.monotonic() - start, 3)
    record: dict[str, object] = {
        "event": "restore",
        "ok": outcome.ok,
        "rtoSeconds": rto_seconds,
    }
    if outcome.manifest is not None:
        record["backupId"] = outcome.manifest.backupId
        record["kekKeyVersion"] = outcome.manifest.encryption.kekKeyVersion
    if outcome.audit_total_records is not None:
        record["auditTotalRecords"] = outcome.audit_total_records
        record["auditVerifiedRecords"] = outcome.audit_verified_records
    if outcome.reconcile is not None:
        record["reconcile"] = {
            "referencedCount": outcome.reconcile.referenced_count,
            "presentCount": outcome.reconcile.present_count,
            "orphansRemoved": len(outcome.reconcile.orphans_removed),
            "missingCount": len(outcome.reconcile.missing),
        }
    if outcome.error is not None:
        record["errorCode"] = outcome.error.code
    if outcome.cleanup_warning is not None:
        record["cleanupWarning"] = outcome.cleanup_warning.code
    log_record(record)
    return 0 if outcome.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="risk_backup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_pg_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--pg-socket-dir", help="local Unix-socket dir for pg_dump/pg_restore")
        p.add_argument("--pg-host")
        p.add_argument("--pg-port")
        p.add_argument("--pg-user")
        p.add_argument("--pg-db")
        p.add_argument("--pg-password")
        p.add_argument(
            "--pg-runner",
            help="argv prefix for pg_dump/pg_restore (e.g. 'docker exec -i <c>')",
        )

    def add_key_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--kek-version", required=True, help="active backup KEK version")
        p.add_argument(
            "--kek-file",
            action="append",
            required=True,
            metavar="VER=PATH",
            help="backup KEK file (base64 32-byte key); repeat for retained decrypt versions",
        )

    pb = sub.add_parser("backup", help="create an encrypted backup artifact")
    pb.add_argument("--type", choices=[t.value for t in BackupType], required=True)
    pb.add_argument("--dsn", required=True)
    add_pg_args(pb)
    pb.add_argument("--storage-root", required=True)
    pb.add_argument("--output", required=True)
    pb.add_argument("--temp-dir", required=True)
    add_key_args(pb)
    pb.add_argument("--quiesce", choices=["none", "compose"], default="none")
    pb.add_argument("--compose-file")
    pb.add_argument("--project-dir", default=".")
    pb.add_argument("--created-by", default="operator")
    pb.add_argument("--trace-id", default="backup")
    pb.set_defaults(func=cmd_backup)

    pr = sub.add_parser("restore", help="restore an artifact into an isolated target")
    pr.add_argument("--artifact", required=True)
    pr.add_argument("--target-dsn", required=True, help="DSN of the isolated empty target DB")
    add_pg_args(pr)
    pr.add_argument("--target-storage-root", required=True)
    pr.add_argument("--temp-dir", required=True)
    add_key_args(pr)
    pr.set_defaults(func=cmd_restore)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BackupError as exc:
        log_record({"event": "fatal", "errorCode": exc.code})
        return 2


__all__ = ["build_parser", "main"]
