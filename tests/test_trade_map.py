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
