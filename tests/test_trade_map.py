from workers.trade_map import deals_to_cashflows, deals_to_trades


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


def test_balance_deal_becomes_deposit_or_withdrawal():
    deals = [
        {"ticket": 50, "order": 0, "position_id": 0, "entry": "in", "type": "2",
         "symbol": "", "price": 0, "volume": 0, "profit": 1000, "swap": 0,
         "commission": 0, "comment": "Deposit", "time": "2026-01-01T09:00:00Z"},
        {"ticket": 51, "order": 0, "position_id": 0, "entry": "in", "type": "2",
         "symbol": "", "price": 0, "volume": 0, "profit": -250, "swap": 0,
         "commission": 0, "comment": "Withdrawal", "time": "2026-01-02T09:00:00Z"},
    ]
    flows = deals_to_cashflows(deals)
    assert len(flows) == 2
    assert flows[0]["op_type"] == "deposit"
    assert flows[0]["amount"] == 1000
    assert flows[1]["op_type"] == "withdrawal"
    assert flows[1]["amount"] == -250
    assert deals_to_trades(deals) == []


def test_balance_deal_with_out_entry_is_not_a_trade():
    deals = [
        {"ticket": 50, "order": 0, "position_id": 0, "entry": "out", "type": "2",
         "symbol": "", "price": 0, "volume": 0, "profit": 1000, "swap": 0,
         "commission": 0, "comment": "Deposit", "time": "2026-01-01T09:00:00Z"},
    ]
    assert deals_to_trades(deals) == []
    assert deals_to_cashflows(deals)[0]["op_type"] == "deposit"
