from workers.trade_map import deals_to_trades


def test_pairs_entry_and_exit():
    deals = [
        {"ticket": 1, "order": 10, "position_id": 99, "entry": "in", "type": "buy",
         "symbol": "EURUSD", "price": 1.10, "volume": 0.5, "profit": 0, "swap": 0,
         "commission": 0, "time": "2026-01-01T10:00:00Z"},
        {"ticket": 2, "order": 11, "position_id": 99, "entry": "out", "type": "sell",
         "symbol": "EURUSD", "price": 1.12, "volume": 0.5, "profit": 100, "swap": -1,
         "commission": -2, "time": "2026-01-01T12:00:00Z"},
    ]
    trades = deals_to_trades(deals)
    assert len(trades) == 1
    t = trades[0]
    assert t["ticket"] == 10  # entry order id (EA semantics)
    assert t["direction"] == "buy"
    assert t["entry_price"] == 1.10
    assert t["exit_price"] == 1.12
    assert t["lot_size"] == 0.5
    assert t["pnl_raw"] == 97


def test_partial_closes_emit_one_trade_per_exit():
    deals = [
        {"ticket": 1, "order": 10, "position_id": 50, "entry": "in", "type": "buy",
         "symbol": "XAUUSD", "price": 2000.0, "volume": 1.0, "profit": 0, "swap": 0,
         "commission": -4, "time": "2026-01-01T10:00:00Z"},
        {"ticket": 2, "order": 11, "position_id": 50, "entry": "out", "type": "sell",
         "symbol": "XAUUSD", "price": 2010.0, "volume": 0.4, "profit": 40, "swap": 0,
         "commission": -1, "time": "2026-01-01T11:00:00Z"},
        {"ticket": 3, "order": 12, "position_id": 50, "entry": "out", "type": "sell",
         "symbol": "XAUUSD", "price": 2020.0, "volume": 0.6, "profit": 120, "swap": -2,
         "commission": -1, "time": "2026-01-01T12:00:00Z"},
    ]
    trades = deals_to_trades(deals)
    assert len(trades) == 2
    assert trades[0]["ticket"] == 11
    assert trades[0]["lot_size"] == 0.4
    assert trades[0]["pnl_raw"] == 35  # 40 - 1 + entry commission -4
    assert trades[1]["ticket"] == 12
    assert trades[1]["lot_size"] == 0.6
    assert trades[1]["pnl_raw"] == 117  # 120 - 2 - 1
    assert trades[0]["direction"] == "buy"
    assert trades[1]["entry_price"] == 2000.0


def test_r_multiple_from_exit_stop():
    deals = [
        {"ticket": 1, "order": 10, "position_id": 99, "entry": "in", "type": "buy",
         "symbol": "EURUSD", "price": 1.10, "volume": 0.5, "profit": 0, "swap": 0,
         "commission": 0, "sl": 1.09, "time": "2026-01-01T10:00:00Z"},
        {"ticket": 2, "order": 11, "position_id": 99, "entry": "out", "type": "sell",
         "symbol": "EURUSD", "price": 1.12, "volume": 0.5, "profit": 100, "swap": 0,
         "commission": 0, "sl": 1.09, "time": "2026-01-01T12:00:00Z"},
    ]
    trades = deals_to_trades(deals)
    assert trades[0]["r_value"] == 2.0
