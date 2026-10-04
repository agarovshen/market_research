from tests.validation.reference import first_difference


def test_reference_report_identifies_first_trade_field_and_delta():
    ours = {"trades": [{"side": "long", "entry_price": 1.1, "pnl": 4.0}],
            "equity": [], "drawdown": [], "statistics": {}}
    reference = {"trades": [{"side": "long", "entry_price": 1.1001, "pnl": 3.0}],
                 "equity": [], "drawdown": [], "statistics": {}}
    difference = first_difference(ours, reference)
    assert difference.location == "Trade #1"
    assert difference.field == "entry_price"
    assert abs(difference.delta + .0001) < 1e-12
    assert "actual:" in str(difference)


def test_reference_tolerance_and_later_sections():
    ours = {"trades": [{"pnl": 1.00001}], "equity": [], "drawdown": [], "statistics": {}}
    ref = {"trades": [{"pnl": 1.0}], "equity": [], "drawdown": [], "statistics": {}}
    assert first_difference(ours, ref, absolute_tolerance=.001) is None
    ours["trades"][0]["pnl"] = ref["trades"][0]["pnl"]
    ours["statistics"] = {"final_equity": 10}
    ref["statistics"] = {"final_equity": 11}
    assert first_difference(ours, ref).field == "final_equity"
