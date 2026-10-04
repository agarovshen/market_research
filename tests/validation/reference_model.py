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


def calculate_single_stop_trade(rows, *, signal_index, side, trigger, stop_loss,
                                quantity=1.0, initial_cash=1_000.0,
                                commission_per_unit=0.0, commission_rate=0.0,
                                slippage=0.0, spread_scale=1.0):
    """Independent primitive oracle for one pending STOP entry and SL exit.

    ``rows`` are plain mappings with time/open/high/low/close/spread keys.
    A STOP becomes eligible on the bar after the signal. The oracle intentionally
    handles only one entry/exit and implements OHLC assumptions here rather
    than importing production models or execution helpers.
    """
    direction = 1 if side == "long" else -1
    entry_row = None
    entry_reference = None
    for row in rows[signal_index + 1:]:
        if side == "long" and row["high"] >= trigger:
            entry_reference = row["open"] if row["open"] >= trigger else trigger
            entry_row = row
            break
        if side == "short" and row["low"] <= trigger:
            entry_reference = row["open"] if row["open"] <= trigger else trigger
            entry_row = row
            break

    if entry_row is None:
        return {"entry": None, "exit": None, "trade": None,
                "final_equity": initial_cash, "final_cash": initial_cash,
                "equity": [initial_cash for _ in rows], "drawdown": [0.0 for _ in rows]}

    def fill(reference, row, action):
        half_spread = row["spread"] * spread_scale / 2
        adverse = direction if action == "open" else -direction
        price = reference + adverse * (half_spread + slippage)
        commission = quantity * (commission_per_unit + abs(price) * commission_rate)
        return price, commission, half_spread * quantity, slippage * quantity

    entry_price, entry_commission, entry_spread, entry_slippage = fill(
        entry_reference, entry_row, "open")
    cash = initial_cash - direction * quantity * entry_price - entry_commission
    equity = []
    entry_index = rows.index(entry_row, signal_index + 1)
    exit_row = None
    exit_reference = None
    for index, row in enumerate(rows):
        if index < entry_index:
            equity.append(initial_cash)
            continue
        if index == entry_index:
            # Entry-bar SL is deliberately not applied; OHLC cannot identify path.
            mark = row["close"] - direction * row["spread"] * spread_scale / 2
            equity.append(cash + direction * quantity * mark)
            continue
        touched = row["low"] <= stop_loss if side == "long" else row["high"] >= stop_loss
        if touched:
            exit_reference = ((row["open"] if row["open"] <= stop_loss else stop_loss)
                              if side == "long" else
                              (row["open"] if row["open"] >= stop_loss else stop_loss))
            exit_row = row
            break
        mark = row["close"] - direction * row["spread"] * spread_scale / 2
        equity.append(cash + direction * quantity * mark)

    if exit_row is None:
        last = rows[-1]
        mark = last["close"] - direction * last["spread"] * spread_scale / 2
        final_equity = cash + direction * quantity * mark
        equity.extend([final_equity] * (len(rows) - len(equity)))
        return {"entry": {"time": entry_row["time"], "price": entry_price,
                           "reference": entry_reference, "commission": entry_commission},
                "exit": None, "trade": None, "final_equity": final_equity,
                "final_cash": cash, "equity": equity, "drawdown": _drawdowns(equity)}

    exit_price, exit_commission, exit_spread, exit_slippage = fill(
        exit_reference, exit_row, "close")
    cash += direction * quantity * exit_price - exit_commission
    gross = (exit_price - entry_price) * quantity * direction
    net = gross - entry_commission - exit_commission
    final_equity = cash
    # Replace the mark on stop bar with realized cash after its exit.
    exit_index = rows.index(exit_row, entry_index + 1)
    equity.extend([final_equity] * (exit_index + 1 - len(equity)))
    equity.extend([final_equity] * (len(rows) - len(equity)))
    return {"entry": {"time": entry_row["time"], "price": entry_price,
                       "reference": entry_reference, "commission": entry_commission,
                       "spread_cost": entry_spread, "slippage_cost": entry_slippage},
            "exit": {"time": exit_row["time"], "price": exit_price,
                      "reference": exit_reference, "commission": exit_commission,
                      "spread_cost": exit_spread, "slippage_cost": exit_slippage},
            "trade": {"side": side, "quantity": quantity, "gross_pnl": gross,
                      "net_pnl": net, "commission": entry_commission + exit_commission},
            "final_equity": final_equity, "final_cash": cash,
            "equity": equity, "drawdown": _drawdowns(equity)}


def _drawdowns(values):
    peak = None
    result = []
    for value in values:
        peak = value if peak is None else max(peak, value)
        result.append(peak - value)
    return result
