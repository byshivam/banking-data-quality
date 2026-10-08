"""Data-quality rules, written as SQL over the raw extract.

Each rule returns the keys of the offending records. Rules are grouped by the
usual quality dimensions so the score can be broken down the same way.

`valid_transactions_sql()` is the QA team's own definition of a "good"
transaction. Reconciliation uses it as an independent oracle to check the
pipeline's output — it is deliberately not shared with the pipeline code.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from .db import AS_OF, CUTOFF_UTC, VALID_CURRENCIES_SQL


@dataclass(frozen=True)
class Rule:
    rule_id: str
    dimension: str
    table: str
    key: str
    description: str
    sql: str


def valid_transactions_sql() -> str:
    """One row per good transaction (any status), with IST date and INR amount."""
    return f"""
    WITH dedup AS (
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY txn_id ORDER BY txn_id) AS rn FROM raw_transactions
        ) WHERE rn = 1
    ),
    typed AS (
        SELECT d.*,
               CAST(replace(d.txn_ts, 'Z', '') AS TIMESTAMP) AS ts_utc,
               TRY_CAST(d.amount AS DECIMAL(18, 2)) AS amt
        FROM dedup d
        WHERE d.currency IN {VALID_CURRENCIES_SQL}
          AND TRY_CAST(d.amount AS DECIMAL(18, 2)) > 0
          AND d.account_id IN (SELECT account_id FROM raw_accounts)
          AND CAST(replace(d.txn_ts, 'Z', '') AS TIMESTAMP) < {CUTOFF_UTC}
    )
    SELECT t.txn_id, t.account_id, t.direction, t.status, t.currency, t.amt,
           CAST(t.ts_utc + INTERVAL 330 MINUTE AS DATE) AS ist_date,
           CASE WHEN t.currency = 'INR' THEN t.amt
                ELSE ROUND(t.amt * CAST(fx.inr_rate AS DECIMAL(12, 4)), 2) END AS inr_amount
    FROM typed t
    LEFT JOIN raw_fx_rates fx
      ON fx.currency = t.currency AND CAST(fx.rate_date AS DATE) = CAST(t.ts_utc + INTERVAL 330 MINUTE AS DATE)
    """


def expected_balances_sql() -> str:
    return f"""
    WITH v AS ({valid_transactions_sql()}),
    net AS (
        SELECT account_id,
               SUM(CASE WHEN direction = 'CREDIT' THEN inr_amount ELSE 0 END) AS credits,
               SUM(CASE WHEN direction = 'DEBIT' THEN inr_amount ELSE 0 END) AS debits
        FROM v WHERE status = 'SUCCESS' GROUP BY account_id
    )
    SELECT a.account_id,
           CAST(a.opening_balance AS DECIMAL(18, 2)) AS opening,
           COALESCE(n.credits, 0) AS credits,
           COALESCE(n.debits, 0) AS debits,
           CAST(a.opening_balance AS DECIMAL(18, 2)) + COALESCE(n.credits, 0) - COALESCE(n.debits, 0) AS expected_closing
    FROM raw_accounts a LEFT JOIN net n USING (account_id)
    """


RULES: list[Rule] = [
    Rule("UNQ-01", "Uniqueness", "transactions", "txn_id",
         "Each txn_id appears once",
         "SELECT txn_id FROM raw_transactions GROUP BY txn_id HAVING COUNT(*) > 1"),
    Rule("CMP-01", "Completeness", "customers", "customer_id",
         "Customer has email, phone and KYC status",
         """SELECT customer_id FROM raw_customers
            WHERE COALESCE(TRIM(email), '') = '' OR COALESCE(TRIM(phone), '') = ''
               OR COALESCE(TRIM(kyc_status), '') = ''"""),
    Rule("VAL-01", "Validity", "transactions", "txn_id",
         "Currency is INR or a currency with an FX rate",
         f"SELECT DISTINCT txn_id FROM raw_transactions WHERE currency IS NULL OR currency NOT IN {VALID_CURRENCIES_SQL}"),
    Rule("VAL-02", "Validity", "transactions", "txn_id",
         "Amount is a number greater than zero",
         "SELECT DISTINCT txn_id FROM raw_transactions WHERE COALESCE(TRY_CAST(amount AS DECIMAL(18, 2)), 0) <= 0"),
    Rule("VAL-03", "Validity", "customers", "customer_id",
         "Email is well formed",
         r"""SELECT customer_id FROM raw_customers
            WHERE COALESCE(TRIM(email), '') <> ''
              AND NOT regexp_full_match(email, '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')"""),
    Rule("VAL-04", "Validity", "customers", "customer_id",
         "Phone is a 10-digit Indian mobile number (starts 6-9)",
         """SELECT customer_id FROM raw_customers
            WHERE COALESCE(TRIM(phone), '') <> '' AND NOT regexp_full_match(phone, '[6-9][0-9]{9}')"""),
    Rule("INT-01", "Integrity", "transactions", "txn_id",
         "Transaction belongs to a known account",
         "SELECT DISTINCT txn_id FROM raw_transactions WHERE account_id NOT IN (SELECT account_id FROM raw_accounts)"),
    Rule("INT-02", "Integrity", "accounts", "account_id",
         "Account's branch exists in the branch master",
         "SELECT account_id FROM raw_accounts WHERE branch_code NOT IN (SELECT branch_code FROM raw_branches)"),
    Rule("TML-01", "Timeliness", "transactions", "txn_id",
         "Transaction is not dated after the extract cutoff",
         f"SELECT DISTINCT txn_id FROM raw_transactions WHERE CAST(replace(txn_ts, 'Z', '') AS TIMESTAMP) >= {CUTOFF_UTC}"),
    Rule("CON-01", "Consistency", "accounts", "account_id",
         "Savings account closing balance is not negative",
         """SELECT c.account_id FROM raw_closing_balances c JOIN raw_accounts a USING (account_id)
            WHERE a.account_type = 'SAVINGS' AND CAST(c.closing_balance AS DECIMAL(18, 2)) < 0"""),
    Rule("CON-02", "Consistency", "accounts", "account_id",
         "Closing balance = opening + credits - debits (successful, valid transactions)",
         f"""WITH e AS ({expected_balances_sql()})
            SELECT c.account_id FROM raw_closing_balances c JOIN e USING (account_id)
            WHERE CAST(c.closing_balance AS DECIMAL(18, 2)) <> e.expected_closing"""),
    Rule("CON-03", "Consistency", "customers", "customer_id",
         "Customers under 18 cannot hold a CURRENT account",
         f"""SELECT DISTINCT c.customer_id FROM raw_customers c JOIN raw_accounts a USING (customer_id)
            WHERE a.account_type = 'CURRENT'
              AND TRY_CAST(c.date_of_birth AS DATE) > {AS_OF} - INTERVAL 18 YEAR"""),
]

RULES_BY_ID = {r.rule_id: r for r in RULES}


def run_rule(con: duckdb.DuckDBPyConnection, rule: Rule) -> list[str]:
    return sorted(row[0] for row in con.execute(rule.sql).fetchall())


def run_all(con: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    return {r.rule_id: run_rule(con, r) for r in RULES}


def table_rows(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM raw_{table}").fetchone()[0]
