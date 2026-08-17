"""AES-256-GCM decrypt matching finhubkh_journal/backend/api/crypto-helper.mjs."""

from __future__ import annotations

import base64
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HEX_KEY_RE = re.compile(r"^[0-9a-f]{64}$", re.I)
IV_LEN = 12
TAG_LEN = 16


def _load_key(secret_hex: str) -> bytes:
    if not secret_hex or not HEX_KEY_RE.match(secret_hex):
        raise ValueError("Encryption key must be a 64-character hex string (32 bytes)")
    return bytes.fromhex(secret_hex)


def decrypt_secret(payload: str, secret_hex: str) -> str:
    """Payload format: base64( iv[12] || authTag[16] || ciphertext )."""
    key = _load_key(secret_hex)
    raw = base64.b64decode(payload)
    if len(raw) < IV_LEN + TAG_LEN + 1:
        raise ValueError("Invalid ciphertext payload")
    iv = raw[:IV_LEN]
    auth_tag = raw[IV_LEN : IV_LEN + TAG_LEN]
    ciphertext = raw[IV_LEN + TAG_LEN :]
    # cryptography AESGCM expects ciphertext || tag
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext + auth_tag, None)
    return plaintext.decode("utf-8")
