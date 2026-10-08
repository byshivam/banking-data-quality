"""Load the raw extract into DuckDB exactly as delivered (every column VARCHAR).

Keeping raw columns as text means a bad value such as currency "" or amount
"-0.00" reaches the checks instead of being lost by a type cast on load.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

TABLES = ("branches", "customers", "accounts", "transactions", "fx_rates", "closing_balances")
VALID_CURRENCIES_SQL = "(SELECT 'INR' AS c UNION SELECT DISTINCT currency FROM raw_fx_rates)"
# Extract cutoff: 1 Oct 2026 00:00 IST = 30 Sep 2026 18:30 UTC.
CUTOFF_UTC = "TIMESTAMP '2026-09-30 18:30:00'"
AS_OF = "DATE '2026-09-30'"


def connect(path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path))


def load_raw(con: duckdb.DuckDBPyConnection, raw_dir: str | Path) -> None:
    raw_dir = Path(raw_dir)
    for name in TABLES:
        csv = (raw_dir / f"{name}.csv").as_posix()
        con.execute(
            f"CREATE OR REPLACE TABLE raw_{name} AS "
            f"SELECT * FROM read_csv('{csv}', header = true, all_varchar = true)"
        )
