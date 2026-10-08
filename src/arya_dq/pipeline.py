"""The ETL pipeline under test: raw extract -> staging -> daily reports.

    stg_transactions          clean transactions with IST date, INR amount, branch
    quarantine_transactions   rejected rows with a reason (nothing is dropped silently)
    rpt_daily_branch_summary  per IST date and branch: count, credits, debits, net (SUCCESS only)
    rpt_account_balances      opening + credits - debits vs the source closing balance

This is the "system under test", written the way a data team would write it.
Like the payments API, it ships with planted bugs that the QA suite must
catch, switched on with PIPELINE_BUGS (none | all | PIPE-01,PIPE-03 ...).
"""

from __future__ import annotations

import os

import duckdb

from .db import CUTOFF_UTC, VALID_CURRENCIES_SQL

BUG_CATALOG: dict[str, dict[str, str]] = {
    "PIPE-01": {"title": "Duplicates not removed",
                "impact": "A re-sent transaction is counted twice in the daily totals."},
    "PIPE-02": {"title": "FX uses the latest rate for every day",
                "impact": "Foreign remittances are converted at the 30 Sep rate instead of the rate on their own date."},
    "PIPE-03": {"title": "Report date taken in UTC, not IST",
                "impact": "Payments made between 00:00 and 05:30 IST land on the previous day's report."},
    "PIPE-04": {"title": "Inner join to branch master",
                "impact": "Transactions of accounts with an unknown branch vanish without being quarantined."},
    "PIPE-05": {"title": "Failed and reversed payments counted",
                "impact": "Totals include money that never moved."},
    "PIPE-06": {"title": "Paise dropped",
                "impact": "Amounts are floored to whole rupees before totalling."},
}


def active_bugs() -> frozenset[str]:
    raw = (os.environ.get("PIPELINE_BUGS") or "none").strip()
    if raw.lower() in ("", "none", "off", "0"):
        return frozenset()
    if raw.lower() == "all":
        return frozenset(BUG_CATALOG)
    ids = {p.strip().upper() for p in raw.split(",") if p.strip()}
    unknown = ids - set(BUG_CATALOG)
    if unknown:
        raise ValueError(f"Unknown pipeline bug id(s): {', '.join(sorted(unknown))}")
    return frozenset(ids)


def run(con: duckdb.DuckDBPyConnection, bugs: frozenset[str] | None = None) -> None:
    bugs = active_bugs() if bugs is None else bugs
    on = bugs.__contains__

    dup_check = "FALSE" if on("PIPE-01") else "dup_rank > 1"
    amount_expr = "CAST(FLOOR(amt) AS DECIMAL(18, 2))" if on("PIPE-06") else "amt"
    date_expr = "CAST(ts_utc AS DATE)" if on("PIPE-03") else "CAST(ts_utc + INTERVAL 330 MINUTE AS DATE)"
    branch_join = "JOIN raw_branches b" if on("PIPE-04") else "LEFT JOIN raw_branches b"
    if on("PIPE-02"):
        fx_join = """LEFT JOIN (SELECT currency, inr_rate FROM raw_fx_rates
                                WHERE rate_date = (SELECT MAX(rate_date) FROM raw_fx_rates)) fx
                     ON fx.currency = c.currency"""
    else:
        fx_join = "LEFT JOIN raw_fx_rates fx ON fx.currency = c.currency AND CAST(fx.rate_date AS DATE) = c.ist_date"
    status_filter = "TRUE" if on("PIPE-05") else "status = 'SUCCESS'"

    con.execute(f"""
        CREATE OR REPLACE TABLE classified AS
        WITH src AS (
            SELECT *,
                   row_number() OVER () AS src_row,
                   TRY_CAST(amount AS DECIMAL(18, 2)) AS amt,
                   TRY_CAST(replace(txn_ts, 'Z', '') AS TIMESTAMP) AS ts_utc
            FROM raw_transactions
        ),
        ranked AS (
            SELECT *, row_number() OVER (PARTITION BY txn_id ORDER BY src_row) AS dup_rank FROM src
        )
        SELECT *,
               CASE
                 WHEN {dup_check} THEN 'DUPLICATE'
                 WHEN currency IS NULL OR currency NOT IN {VALID_CURRENCIES_SQL} THEN 'INVALID_CURRENCY'
                 WHEN amt IS NULL OR amt <= 0 THEN 'NON_POSITIVE_AMOUNT'
                 WHEN account_id NOT IN (SELECT account_id FROM raw_accounts) THEN 'UNKNOWN_ACCOUNT'
                 WHEN ts_utc IS NULL OR ts_utc >= {CUTOFF_UTC} THEN 'FUTURE_DATED'
               END AS reject_reason,
               {date_expr} AS ist_date
        FROM ranked
    """)

    con.execute("""
        CREATE OR REPLACE TABLE quarantine_transactions AS
        SELECT txn_id, account_id, txn_ts, amount, currency, direction, channel, status, reject_reason
        FROM classified WHERE reject_reason IS NOT NULL
    """)

    con.execute(f"""
        CREATE OR REPLACE TABLE stg_transactions AS
        SELECT c.txn_id, c.account_id, a.branch_code, b.branch_name, c.ist_date, c.direction, c.channel, c.status, c.currency,
               {amount_expr.replace('amt', 'c.amt')} AS amount,
               CASE WHEN c.currency = 'INR' THEN {amount_expr.replace('amt', 'c.amt')}
                    ELSE ROUND({amount_expr.replace('amt', 'c.amt')} * CAST(fx.inr_rate AS DECIMAL(12, 4)), 2)
               END AS inr_amount
        FROM classified c
        JOIN raw_accounts a ON a.account_id = c.account_id
        {branch_join} ON b.branch_code = a.branch_code
        {fx_join}
        WHERE c.reject_reason IS NULL
    """)

    con.execute(f"""
        CREATE OR REPLACE TABLE rpt_daily_branch_summary AS
        SELECT ist_date AS report_date, branch_code,
               COUNT(*) AS txn_count,
               SUM(CASE WHEN direction = 'CREDIT' THEN inr_amount ELSE 0 END) AS credit_inr,
               SUM(CASE WHEN direction = 'DEBIT' THEN inr_amount ELSE 0 END) AS debit_inr,
               SUM(CASE WHEN direction = 'CREDIT' THEN inr_amount ELSE -inr_amount END) AS net_inr
        FROM stg_transactions
        WHERE {status_filter}
        GROUP BY ALL
        ORDER BY report_date, branch_code
    """)

    con.execute(f"""
        CREATE OR REPLACE TABLE rpt_account_balances AS
        WITH net AS (
            SELECT account_id,
                   SUM(CASE WHEN direction = 'CREDIT' THEN inr_amount ELSE 0 END) AS credits,
                   SUM(CASE WHEN direction = 'DEBIT' THEN inr_amount ELSE 0 END) AS debits
            FROM stg_transactions WHERE {status_filter} GROUP BY account_id
        )
        SELECT a.account_id,
               CAST(a.opening_balance AS DECIMAL(18, 2)) AS opening,
               COALESCE(n.credits, 0) AS credits,
               COALESCE(n.debits, 0) AS debits,
               CAST(a.opening_balance AS DECIMAL(18, 2)) + COALESCE(n.credits, 0) - COALESCE(n.debits, 0) AS computed_closing,
               CAST(cb.closing_balance AS DECIMAL(18, 2)) AS source_closing,
               CAST(cb.closing_balance AS DECIMAL(18, 2))
                 - (CAST(a.opening_balance AS DECIMAL(18, 2)) + COALESCE(n.credits, 0) - COALESCE(n.debits, 0)) AS difference
        FROM raw_accounts a
        LEFT JOIN net n USING (account_id)
        LEFT JOIN raw_closing_balances cb USING (account_id)
        ORDER BY a.account_id
    """)
    con.execute("DROP TABLE classified")
