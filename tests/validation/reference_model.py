"""Tiny test-only accounting oracle for scripted, next-bar-open scenarios.

Inputs are primitive rows and event tuples. This intentionally models only the
small subset used by validation fixtures; it imports no application code.
"""


def calculate(rows, events, *, initial_cash=1_000.0, quantity=1.0,
              commission_per_unit=0.0, commission_rate=0.0, slippage=0.0,
              spread_scale=1.0, close_at_end=True):
    """Calculate a scripted market-order path independently.

    A mapping entry ``index: ("open", side, qty_or_none)`` or
    ``index: ("close", None, None)`` is an intent emitted after that bar.
    It fills at the next bar open. Open positions are valued at the executable
    close side; optional final liquidation uses the final close.
    """
    cash = initial_cash
    position = None
    pending = None
    orders, trades, equity = [], [], []

    def execute(action, side, qty, signal_row, fill_row, reference):
        nonlocal cash
        direction = 1 if side == "long" else -1
        half_spread = fill_row["spread"] * spread_scale / 2
        adverse = direction if action == "open" else -direction
        price = reference + adverse * (half_spread + slippage)
        commission = qty * (commission_per_unit + abs(price) * commission_rate)
        spread_fee = half_spread * qty
        slip_fee = slippage * qty
        if action == "open":
            cash -= direction * qty * price + commission
        else:
            cash += direction * qty * price - commission
        order = {"action": action, "side": side, "quantity": qty,
                 "signal_time": signal_row["time"], "time": fill_row["time"],
                 "reference_price": reference, "price": price,
                 "commission": commission, "fees": spread_fee,
                 "slippage": slip_fee}
        orders.append(order)
        return order

    def close(fill_row, reference, signal_row):
        nonlocal position
        old = position
        order = execute("close", old["side"], old["quantity"], signal_row,
                        fill_row, reference)
        direction = 1 if old["side"] == "long" else -1
        gross = (order["price"] - old["price"]) * old["quantity"] * direction
        net = gross - old["commission"] - order["commission"]
        trades.append({"side": old["side"], "entry_time": old["time"],
                       "entry_price": old["price"], "exit_time": fill_row["time"],
                       "exit_price": order["price"], "quantity": old["quantity"],
                       "commission": old["commission"] + order["commission"],
                       "fees": old["fees"] + order["fees"],
                       "slippage": old["slippage"] + order["slippage"],
                       "gross_pnl": gross, "net_pnl": net})
        position = None

    for index, row in enumerate(rows):
        if pending is not None:
            intent, signal_row = pending
            action, side, requested_qty = intent
            if action == "open" and position is None:
                qty = quantity if requested_qty is None else requested_qty
                order = execute("open", side, qty, signal_row, row, row["open"])
                position = {"side": side, "quantity": qty, "price": order["price"],
                            "time": row["time"], "commission": order["commission"],
                            "fees": order["fees"], "slippage": order["slippage"]}
            elif action == "close" and position is not None:
                close(row, row["open"], signal_row)

        current = events.get(index)
        pending = (current, row) if current is not None else None
        if position is None:
            marked = cash
        else:
            direction = 1 if position["side"] == "long" else -1
            liquidation = row["close"] - direction * row["spread"] * spread_scale / 2
            marked = cash + direction * position["quantity"] * liquidation
        equity.append({"timestamp": row["time"], "value": marked})

    if position is not None and close_at_end and rows:
        final = rows[-1]
        close(final, final["close"], final)
        equity[-1] = {"timestamp": final["time"], "value": cash}

    peak = None
    drawdown = []
    for point in equity:
        peak = point["value"] if peak is None else max(peak, point["value"])
        drawdown.append({"timestamp": point["timestamp"], "value": peak - point["value"]})

    final_equity = cash
    unrealized = 0.0
    if position is not None and rows:
        last = rows[-1]
        direction = 1 if position["side"] == "long" else -1
        liquidation = last["close"] - direction * last["spread"] * spread_scale / 2
        final_equity += direction * position["quantity"] * liquidation
        unrealized = ((liquidation - position["price"])
                      * position["quantity"] * direction)
    return {"orders": orders, "trades": trades, "equity": equity,
            "drawdown": drawdown, "statistics": {"final_equity": final_equity},
            "final_cash": cash, "unrealized_pnl": unrealized,
            "final_equity": final_equity}
