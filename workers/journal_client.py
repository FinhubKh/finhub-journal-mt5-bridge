import httpx


def post_trades(client: httpx.Client, url: str, token: str, trading_account_id: str, trades: list):
    return client.post(
        url,
        headers={"Content-Type": "application/json", "x-bridge-token": token},
        json={"trading_account_id": trading_account_id, "trades": trades},
        timeout=60.0,
    )


def post_error(client: httpx.Client, url: str, token: str, trading_account_id: str, error: str):
    return client.post(
        url,
        headers={"Content-Type": "application/json", "x-bridge-token": token},
        json={"trading_account_id": trading_account_id, "error": error[:500]},
        timeout=30.0,
    )
