import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from workers.crypto_helper import decrypt_secret


def _encrypt_like_journal(plaintext: str, secret_hex: str) -> str:
    key = bytes.fromhex(secret_hex)
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # cryptography returns ciphertext||tag; journal stores iv||tag||ciphertext
    ciphertext, tag = ct[:-16], ct[-16:]
    return base64.b64encode(iv + tag + ciphertext).decode("ascii")


def test_decrypt_secret_matches_journal_payload_format():
    key = "a" * 64
    payload = _encrypt_like_journal("investor-pass", key)
    assert decrypt_secret(payload, key) == "investor-pass"
