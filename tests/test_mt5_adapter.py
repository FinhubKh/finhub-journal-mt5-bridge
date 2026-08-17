from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from workers.mt5_adapter import (
    HISTORY_CHUNK_DAYS,
    MetaTrader5Adapter,
    keep_history_deal,
    map_history_deal,
)


def _deal(**overrides):
    base = dict(
        ticket=1,
        order=10,
        position_id=1,
        entry=0,
        type=0,
        symbol="EURUSD",
        price=1.1,
        volume=0.1,
        profit=0,
        swap=0,
        commission=0,
        sl=0,
        comment="",
        time="2026-01-01T10:00:00Z",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeMt5Lib:
    def __init__(self, deals=None):
        self.deals = list(deals or [])
        self.calls = []

    def history_deals_get(self, date_from, date_to):
        self.calls.append((date_from, date_to))
        return list(self.deals)


def test_keep_buy_even_when_type_is_zero():
    assert keep_history_deal(0, 0) is True
    assert keep_history_deal(1, 1) is True


def test_keep_balance_deal_with_non_in_out_entry():
    assert keep_history_deal(2, 2) is True
    assert keep_history_deal(3, 6) is True
    assert keep_history_deal(2, 0) is False


def test_map_buy_and_balance_types():
    buy = map_history_deal(_deal(type=0))
    assert buy["type"] == "buy"
    assert buy["entry"] == "in"
    balance = map_history_deal(_deal(ticket=50, type=2, profit=1000, comment="Deposit"))
    assert balance["type"] == "2"
    assert balance["profit"] == 1000
    assert balance["comment"] == "Deposit"


def test_adapter_keeps_balance_deals_and_maps_type_2():
    lib = FakeMt5Lib(
        deals=[
            _deal(ticket=50, type=2, entry=2, profit=500, comment="Deposit"),
            _deal(ticket=2, type=0, entry=0),
        ]
    )
    adapter = MetaTrader5Adapter(mt5=lib)
    now = datetime.now(timezone.utc)
    rows = adapter.history_deals(now - timedelta(days=1), now)
    by_ticket = {row["ticket"]: row for row in rows}
    assert by_ticket[50]["type"] == "2"
    assert by_ticket[50]["profit"] == 500
    assert by_ticket[2]["type"] == "buy"


def test_adapter_chunks_multi_year_history_and_dedupes_tickets():
    lib = FakeMt5Lib(deals=[_deal(ticket=50, type=2, profit=1000)])
    adapter = MetaTrader5Adapter(mt5=lib)
    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=3650)
    rows = adapter.history_deals(date_from, date_to)
    assert len(lib.calls) >= (3650 // HISTORY_CHUNK_DAYS)
    assert lib.calls[0][0] == date_from
    assert lib.calls[-1][1] == date_to
    assert [row["ticket"] for row in rows] == [50]
