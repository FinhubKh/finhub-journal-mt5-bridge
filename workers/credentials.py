"""Resolve login/server/password for a bridge job.

Prefer credentials already on the job (legacy / tests / short-TTL Redis secret).
Otherwise load encrypted investor credentials from Supabase and decrypt.
"""

from __future__ import annotations

import httpx

from workers.crypto_helper import decrypt_secret
from workers.supabase_client import fetch_investor_credentials


class CredentialsError(Exception):
    """Missing or undecryptable investor credentials."""


def resolve_job_credentials(
    http: httpx.Client,
    *,
    job: dict,
    supabase_url: str,
    service_key: str,
    encryption_key: str,
) -> dict:
    out = dict(job)
    has_inline = bool(out.get("password") and out.get("login") and out.get("server"))
    if has_inline:
        return out

    trading_account_id = str(out.get("trading_account_id") or "")
    if not trading_account_id:
        raise CredentialsError("trading_account_id is required")

    if not encryption_key:
        raise CredentialsError("INVESTOR_CRED_ENCRYPTION_KEY is not configured on the bridge")

    creds = fetch_investor_credentials(
        http,
        supabase_url=supabase_url,
        service_key=service_key,
        trading_account_id=trading_account_id,
    )
    if not creds:
        raise CredentialsError("No investor credentials found for this account")

    try:
        password = decrypt_secret(str(creds.get("encrypted_password") or ""), encryption_key)
    except Exception as exc:
        raise CredentialsError("Could not decrypt stored credentials") from exc

    out["login"] = str(creds.get("login") or "")
    out["server"] = str(creds.get("broker_server") or "")
    out["password"] = password
    if not out["login"] or not out["server"] or not out["password"]:
        raise CredentialsError("Stored investor credentials are incomplete")
    return out
