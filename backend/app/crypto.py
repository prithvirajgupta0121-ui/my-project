"""Encryption-at-rest helpers built on `cryptography` (Fernet: AES-128-CBC + HMAC-SHA256).

No custom cryptography is implemented here; we only wrap the library.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

from .config import get_settings, load_or_create_secret

_fernet: Fernet | None = None
MAGIC = b"AEGISENC1"  # file header marking encrypted blobs


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        s = get_settings()
        key = s.encryption_key or load_or_create_secret(
            s, "fernet.key", lambda: Fernet.generate_key().decode()
        )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_bytes(data: bytes) -> bytes:
    return MAGIC + fernet().encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    if data.startswith(MAGIC):
        return fernet().decrypt(data[len(MAGIC):])
    return data  # plaintext (encryption disabled when written)


def write_secure_file(path: Path, data: bytes, encrypt: bool | None = None) -> None:
    """Write a file with 0600 permissions, encrypting if configured."""
    if encrypt is None:
        encrypt = get_settings().encrypt_at_rest
    payload = encrypt_bytes(data) if encrypt else data
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)


def read_secure_file(path: Path) -> bytes:
    return decrypt_bytes(path.read_bytes())


def is_encrypted_file(path: Path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(len(MAGIC)) == MAGIC


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EncryptedText(TypeDecorator):
    """SQLAlchemy column type that transparently encrypts text values."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        if not get_settings().encrypt_at_rest:
            return value
        return "enc:" + fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None or not value.startswith("enc:"):
            return value
        try:
            return fernet().decrypt(value[4:].encode()).decode()
        except InvalidToken:
            return "[UNREADABLE — ENCRYPTION KEY MISMATCH]"
