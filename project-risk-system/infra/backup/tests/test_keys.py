"""Backup KEK / DEK wrap-unwrap (rpenc envelope, versioned key ring)."""

from __future__ import annotations

import base64
from collections.abc import Callable

import pytest

from risk_backup.errors import BackupError
from risk_backup.keys import BackupKeyRing, new_dek, unwrap_dek, wrap_dek

KeyRingFactory = Callable[[str, list[str]], BackupKeyRing]


def test_wrap_unwrap_round_trips_dek(default_key_ring: BackupKeyRing) -> None:
    dek = new_dek()
    wrapped = wrap_dek(default_key_ring, dek)
    assert wrapped.startswith("rpenc:v1:v1:")
    assert unwrap_dek(default_key_ring, wrapped, "v1") == dek


def test_wrapped_dek_envelope_carries_kek_version(default_key_ring: BackupKeyRing) -> None:
    dek = new_dek()
    wrapped = wrap_dek(default_key_ring, dek)
    assert wrapped.split(":")[2] == "v1"
    # The wrapped envelope must not leak the DEK or KEK plaintext.
    dek_b64 = base64.b64encode(dek).decode("ascii")
    assert dek_b64 not in wrapped


def test_historical_kek_version_decrypts_without_reencryption(
    key_ring_factory: KeyRingFactory,
) -> None:
    ring_v1 = key_ring_factory("v1", ["v1"])
    dek = new_dek()
    wrapped = wrap_dek(ring_v1, dek)
    # Later, active version is v2 but v1 is retained for decrypt.
    ring_rotated = key_ring_factory("v2", ["v1", "v2"])
    assert unwrap_dek(ring_rotated, wrapped, "v1") == dek


def test_wrong_header_version_fails_closed(default_key_ring: BackupKeyRing) -> None:
    dek = new_dek()
    wrapped = wrap_dek(default_key_ring, dek)
    with pytest.raises(BackupError, match="KEK_VERSION_MISMATCH"):
        unwrap_dek(default_key_ring, wrapped, "v2")


def test_missing_kek_version_fails_closed(key_ring_factory: KeyRingFactory) -> None:
    ring_v1 = key_ring_factory("v1", ["v1"])
    dek = new_dek()
    wrapped = wrap_dek(ring_v1, dek)
    # A keystore that no longer retains v1 cannot unwrap.
    ring_v2_only = key_ring_factory("v2", ["v2"])
    with pytest.raises(BackupError, match=r"DEK_UNWRAP_FAILED|KEK_VERSION_MISMATCH"):
        unwrap_dek(ring_v2_only, wrapped, "v1")


def test_tampered_wrapped_dek_fails_closed(default_key_ring: BackupKeyRing) -> None:
    dek = new_dek()
    wrapped = wrap_dek(default_key_ring, dek)
    parts = wrapped.split(":")
    # Corrupt the ciphertext portion (last field).
    corrupt = parts[4][:-2] + ("AA" if parts[4][-2:] != "AA" else "BB")
    tampered = ":".join([parts[0], parts[1], parts[2], parts[3], corrupt])
    with pytest.raises(BackupError, match="DEK_UNWRAP_FAILED"):
        unwrap_dek(default_key_ring, tampered, "v1")
