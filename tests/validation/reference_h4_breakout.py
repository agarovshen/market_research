"""Primitive-only oracle for the tested H4 two-candle breakout contract.

This module intentionally imports no application code. It derives rule
conditions, STOP intents, position stops, fills, and PnL from dictionary rows.
It models only one instrument, one pending entry, one position, and no costs.
"""


def _normal(row):
    width = row["high"] - row["low"]
    return width > 0 and abs(row["close"] - row["open"]) / width >= 0.5


def _setup(first, second):
    common = {"first_normal": _normal(first), "second_normal": _normal(second)}
    if (first["close"] > first["open"]
            and second["close"] > second["open"]):
        conditions = {
            **common,
            "first_direction": True,
            "second_direction": True,
            "breakout": second["close"] > first["high"],
        }
        valid = all(conditions.values())
        return conditions | {"side": "long", "valid": valid,
                             "trigger": second["high"],
                             "stop_loss": second["low"]}
    if (first["close"] < first["open"]
            and second["close"] < second["open"]):
        conditions = {
            **common,
            "first_direction": True,
            "second_direction": True,
            "breakout": second["close"] < first["low"],
        }
        valid = all(conditions.values())
        return conditions | {"side": "short", "valid": valid,
                             "trigger": second["low"],
                             "stop_loss": second["high"]}
    return {**common,
            "first_direction": first["close"] < first["open"],
            "second_direction": second["close"] < second["open"],
            "breakout": False, "side": None, "valid": False,
            "trigger": None, "stop_loss": None}


def run(rows, *, quantity=1.0, initial_cash=1_000.0):
    """Run explicit H4 rules and supplied STOP/SL bar assumptions on dicts."""
    pending = None
    position = None
    setup_checks = []
    stop_updates = []
    signals = []
    cash = initial_cash
    entry = exit_ = trade = None

    for index, row in enumerate(rows):
        if position is not None and index > position["index"]:
            stop = position["stop_loss"]
            touched = (row["low"] <= stop if position["side"] == "long"
                       else row["high"] >= stop)
            if touched:
                if position["side"] == "long":
                    reference = row["open"] if row["open"] <= stop else stop
                    direction = 1
                else:
                    reference = row["open"] if row["open"] >= stop else stop
                    direction = -1
                exit_ = {"index": index, "time": row["time"],
                         "reference_price": reference, "stop_loss": stop,
                         "reason": "stop_loss"}
                gross = (reference - position["price"]) * quantity * direction
                cash += direction * quantity * reference
                trade = {"side": position["side"], "quantity": quantity,
                         "entry_time": position["time"],
                         "entry_price": position["price"],
                         "exit_time": row["time"], "exit_price": reference,
                         "gross_pnl": gross, "net_pnl": gross,
                         "final_equity": cash}
                position = None

        if pending is not None and index > pending["index"] and position is None:
            if pending["side"] == "long" and row["high"] >= pending["trigger"]:
                reference = row["open"] if row["open"] >= pending["trigger"] else pending["trigger"]
            elif pending["side"] == "short" and row["low"] <= pending["trigger"]:
                reference = row["open"] if row["open"] <= pending["trigger"] else pending["trigger"]
            else:
                reference = None
            if reference is not None:
                direction = 1 if pending["side"] == "long" else -1
                cash -= direction * quantity * reference
                entry = {"index": index, "time": row["time"],
                         "reference_price": reference, "side": pending["side"],
                         "quantity": quantity,
                         "initial_stop_loss": pending["stop_loss"],
                         "trigger": pending["trigger"]}
                position = {"index": index, "time": row["time"],
                            "price": reference, "side": pending["side"],
                            "stop_loss": pending["stop_loss"]}
                pending = None

        # This models the signal callback after the engine processes this bar.
        if position is not None:
            if index > position["index"]:
                prior = rows[index - 1]
                updated = (prior["low"] if position["side"] == "long"
                           else prior["high"])
                stop_updates.append({"index": index, "old": position["stop_loss"],
                                     "new": updated})
                position["stop_loss"] = updated
            continue
        if pending is not None or index == 0:
            continue

        check = _setup(rows[index - 1], row)
        setup_checks.append({"index": index, **check})
        if check["valid"]:
            signal = {"index": index, "side": check["side"],
                      "trigger": check["trigger"],
                      "stop_loss": check["stop_loss"]}
            signals.append(signal)
            pending = signal

    return {"setups": setup_checks, "signals": signals,
            "stop_updates": stop_updates, "entry": entry, "exit": exit_,
            "trade": trade, "final_cash": cash,
            "open_position": position}
