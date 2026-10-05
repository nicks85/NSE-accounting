"""Synthetic Zerodha tradebook generator. Never real data: fake symbols and ISINs only.

The layout follows importers/zerodha.py, which is itself unconfirmed (Q-015).
"""

import csv
import io
import random
from dataclasses import dataclass

HEADER = ["symbol", "isin", "trade_date", "exchange", "segment", "series", "trade_type",
          "auction", "quantity", "price", "trade_id", "order_id", "order_execution_time"]

SYNTHETIC = {"SYNTHA": "INE000A01011", "SYNTHB": "INE000B01012", "SYNTHC": "INE000C01013"}


@dataclass(frozen=True)
class Row:
    symbol: str
    trade_date: str
    side: str
    quantity: str
    price: str
    time: str = "09:30:00"
    segment: str = "EQ"
    exchange: str = "NSE"


def tradebook_csv(rows: list[Row], *, header: list[str] | None = None) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header or HEADER)
    for n, row in enumerate(rows, start=1):
        isin = SYNTHETIC.get(row.symbol, "") if row.segment == "EQ" else ""
        writer.writerow([
            row.symbol, isin, row.trade_date, row.exchange, row.segment,
            "EQ" if row.segment == "EQ" else "", row.side.lower(), "false", row.quantity,
            row.price, f"{n:08d}", f"1{n:018d}", f"{row.trade_date}T{row.time}",
        ])
    return out.getvalue()


def random_rows(seed: int, count: int) -> list[Row]:
    """Random delivery round trips that never oversell, for property tests."""
    rng = random.Random(seed)
    held = dict.fromkeys(SYNTHETIC, 0)
    rows = []
    for i in range(count):
        symbol = rng.choice(list(SYNTHETIC))
        day = f"2025-{4 + i // 28:02d}-{1 + i % 28:02d}"
        if held[symbol] and rng.random() < 0.4:
            qty = rng.randint(1, held[symbol])
            held[symbol] -= qty
            side = "sell"
        else:
            qty = rng.randint(1, 50)
            held[symbol] += qty
            side = "buy"
        price = f"{rng.randint(100, 5000)}.{rng.randint(0, 99):02d}"
        rows.append(Row(symbol, day, side, str(qty), price))
    return rows
