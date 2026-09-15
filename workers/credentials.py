"""Resolve login/server/password for a bridge job.

Prefer credentials already on the job (legacy / tests / short-TTL Redis secret).
Otherwise load encrypted investor credentials from Supabase and decrypt.
"""

from __future__ import annotations

import httpx

from workers.crypto_helper import decrypt_secret
from workers.supabase_client import fetch_investor_credentials, fetch_trading_account


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
    trading_account_id = str(out.get("trading_account_id") or "")
    if trading_account_id and not out.get("platform"):
        try:
            account = fetch_trading_account(
                http,
                supabase_url=supabase_url,
                service_key=service_key,
                trading_account_id=trading_account_id,
            )
            if account and account.get("platform"):
                out["platform"] = str(account.get("platform") or "mt5").lower()
        except Exception:
            out.setdefault("platform", "mt5")
    out.setdefault("platform", "mt5")

    has_inline = bool(out.get("password") and out.get("login") and out.get("server"))
    if has_inline:
        out["server"] = normalize_broker_server(str(out["server"]))
        return out

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
    out["server"] = normalize_broker_server(str(out["server"]))
    return out


# MT4 matches .srv server strings case-sensitively. Map known UI aliases.
_SERVER_ALIASES = {
    "blackwellglobal2-live3": "BlackwellGlobal2-Live3",
}


def normalize_broker_server(server: str) -> str:
    raw = (server or "").strip()
    if not raw:
        return raw
    return _SERVER_ALIASES.get(raw.casefold()) or raw
