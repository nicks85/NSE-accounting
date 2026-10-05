"""Generic synthetic tradebook writer: header names + rows of values. Synthetic data only."""

import csv
import io


def write_csv(header: list[str], rows: list[list[str]], *, preamble: list[list[str]] | None = None
              ) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    for line in preamble or []:
        writer.writerow(line)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


UPSTOX_HEADER = ["trade_date", "exchange", "segment", "scrip_name", "symbol", "isin",
                 "transaction_type", "quantity", "price", "amount", "trade_id", "trade_time"]


def upstox_row(day: str, side: str, qty: int, price: str, *, symbol: str = "SYNTHA",
               isin: str = "INE000A01011", exchange: str = "NSE", segment: str = "EQ",
               trade_id: str = "1", time: str = "09:30:00") -> list[str]:
    return [day, exchange, segment, f"{symbol} SYNTHETIC LTD", symbol, isin, side, str(qty),
            price, "", trade_id, time]
