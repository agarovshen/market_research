from datetime import datetime, timedelta

from app.backtest import Bar


START = datetime(2024, 1, 1)


def bars(*prices, spread=0.0, step=timedelta(minutes=1)):
    """Construct explicitly controlled bars; high/low enclose open and close."""
    return tuple(
        Bar(START + step * index, price, price, price, price, spread=spread)
        for index, price in enumerate(prices)
    )
