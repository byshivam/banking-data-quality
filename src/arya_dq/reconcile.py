"""Source-to-report reconciliation.

Every check recomputes its expected figure straight from the raw extract using
the QA team's own SQL (rules.valid_transactions_sql), then compares it with
what the pipeline produced. Nothing here reuses pipeline code, so a bug in the
pipeline can't hide by being repeated in the check.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import duckdb

from .rules import RULES_BY_ID, expected_balances_sql, run_rule, valid_transactions_sql


@dataclass
class Check:
    check_id: str
    name: str
    passed: bool
    expected: str
    actual: str
    mismatches: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _rows(con: duckdb.DuckDBPyConnection, sql: str, limit: int = 10) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [{c: (str(v) if v is not None else None) for c, v in zip(cols, row)} for row in cur.fetchmany(limit)]


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]


def row_accounting(con) -> Check:
    raw = _count(con, "SELECT * FROM raw_transactions")
    stg = _count(con, "SELECT * FROM stg_transactions")
    qua = _count(con, "SELECT * FROM quarantine_transactions")
    return Check("REC-01", "Every raw row is either loaded or quarantined",
                 raw == stg + qua, f"{raw} raw rows", f"{stg} loaded + {qua} quarantined = {stg + qua}")


def valid_rows_loaded_once(con) -> Check:
    oracle = f"SELECT txn_id FROM ({valid_transactions_sql()})"
    missing = f"SELECT txn_id FROM ({oracle}) EXCEPT SELECT txn_id FROM stg_transactions"
    extra = f"SELECT txn_id FROM stg_transactions EXCEPT SELECT txn_id FROM ({oracle})"
    dups = "SELECT txn_id, COUNT(*) AS copies FROM stg_transactions GROUP BY txn_id HAVING COUNT(*) > 1"
    n_missing, n_extra, n_dups = _count(con, missing), _count(con, extra), _count(con, dups)
    mism = ([{"issue": "missing", **r} for r in _rows(con, missing, 5)]
            + [{"issue": "unexpected", **r} for r in _rows(con, extra, 5)]
            + [{"issue": "duplicated", **r} for r in _rows(con, dups, 5)])
    return Check("REC-02", "Each valid transaction is loaded exactly once",
                 n_missing == n_extra == n_dups == 0,
                 f"{_count(con, oracle)} valid transactions",
                 f"{n_missing} missing, {n_extra} unexpected, {n_dups} duplicated", mism)


REASON_RULES = {
    "INVALID_CURRENCY": "VAL-01",
    "NON_POSITIVE_AMOUNT": "VAL-02",
    "UNKNOWN_ACCOUNT": "INT-01",
    "FUTURE_DATED": "TML-01",
}


def quarantine_matches_rules(con) -> Check:
    expected = {"DUPLICATE": con.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT txn_id) FROM raw_transactions").fetchone()[0]}
    for reason, rule_id in REASON_RULES.items():
        expected[reason] = len(run_rule(con, RULES_BY_ID[rule_id]))
    actual = dict(con.execute(
        "SELECT reject_reason, COUNT(*) FROM quarantine_transactions GROUP BY 1").fetchall())
    mism = [{"reason": r, "expected": str(expected.get(r, 0)), "actual": str(actual.get(r, 0))}
            for r in sorted(set(expected) | set(actual)) if expected.get(r, 0) != actual.get(r, 0)]
    return Check("REC-03", "Quarantine counts per reason match the quality rules",
                 not mism, ", ".join(f"{k} {v}" for k, v in expected.items()),
                 ", ".join(f"{k} {v}" for k, v in sorted(actual.items())), mism)


def daily_control_totals(con) -> Check:
    sql = f"""
        WITH o AS (
            SELECT ist_date AS report_date, COUNT(*) AS n,
                   SUM(CASE WHEN direction = 'CREDIT' THEN inr_amount ELSE 0 END) AS cr,
                   SUM(CASE WHEN direction = 'DEBIT' THEN inr_amount ELSE 0 END) AS dr
            FROM ({valid_transactions_sql()}) WHERE status = 'SUCCESS' GROUP BY 1
        ),
        r AS (
            SELECT report_date, SUM(txn_count) AS n, SUM(credit_inr) AS cr, SUM(debit_inr) AS dr
            FROM rpt_daily_branch_summary GROUP BY 1
        )
        SELECT COALESCE(o.report_date, r.report_date) AS report_date,
               o.n AS expected_count, r.n AS report_count,
               o.cr AS expected_credit, r.cr AS report_credit,
               o.dr AS expected_debit, r.dr AS report_debit
        FROM o FULL OUTER JOIN r USING (report_date)
        WHERE o.n IS DISTINCT FROM r.n OR o.cr IS DISTINCT FROM r.cr OR o.dr IS DISTINCT FROM r.dr
        ORDER BY 1
    """
    bad = _count(con, sql)
    days = con.execute("SELECT COUNT(DISTINCT report_date) FROM rpt_daily_branch_summary").fetchone()[0]
    return Check("REC-04", "Daily control totals (count, credits, debits) match the source",
                 bad == 0, "every day matches", f"{bad} of {days} report days differ", _rows(con, sql))


def branch_totals(con) -> Check:
    sql = f"""
        WITH o AS (
            SELECT v.ist_date AS report_date, a.branch_code, COUNT(*) AS n,
                   SUM(CASE WHEN v.direction = 'CREDIT' THEN v.inr_amount ELSE -v.inr_amount END) AS net
            FROM ({valid_transactions_sql()}) v JOIN raw_accounts a USING (account_id)
            WHERE v.status = 'SUCCESS' GROUP BY 1, 2
        )
        SELECT COALESCE(o.report_date, r.report_date) AS report_date,
               COALESCE(o.branch_code, r.branch_code) AS branch_code,
               o.n AS expected_count, r.txn_count AS report_count, o.net AS expected_net, r.net_inr AS report_net
        FROM o FULL OUTER JOIN rpt_daily_branch_summary r
          ON r.report_date = o.report_date AND r.branch_code = o.branch_code
        WHERE o.n IS DISTINCT FROM r.txn_count OR o.net IS DISTINCT FROM r.net_inr
        ORDER BY 1, 2
    """
    bad = _count(con, sql)
    total = _count(con, "SELECT * FROM rpt_daily_branch_summary")
    return Check("REC-05", "Every branch-day row matches the source",
                 bad == 0, "every branch-day matches", f"{bad} branch-day rows differ (report has {total})",
                 _rows(con, sql))


def account_balances(con) -> Check:
    sql = f"""
        SELECT e.account_id, e.expected_closing, r.computed_closing AS report_closing
        FROM ({expected_balances_sql()}) e
        FULL OUTER JOIN rpt_account_balances r USING (account_id)
        WHERE e.expected_closing IS DISTINCT FROM r.computed_closing
        ORDER BY 1
    """
    bad = _count(con, sql)
    return Check("REC-06", "Computed closing balance per account matches the source ledger",
                 bad == 0, "all accounts match", f"{bad} accounts differ", _rows(con, sql))


def balance_breaks(con) -> Check:
    rule = set(run_rule(con, RULES_BY_ID["CON-02"]))
    report = {r[0] for r in con.execute(
        "SELECT account_id FROM rpt_account_balances WHERE difference IS DISTINCT FROM 0").fetchall()}
    mism = ([{"account_id": a, "issue": "break missed by report"} for a in sorted(rule - report)][:5]
            + [{"account_id": a, "issue": "false break in report"} for a in sorted(report - rule)][:5])
    return Check("REC-07", "Balance breaks in the report are exactly the known source breaks",
                 rule == report, f"{len(rule)} accounts (rule CON-02)", f"{len(report)} accounts flagged by report", mism)


CHECKS = [row_accounting, valid_rows_loaded_once, quarantine_matches_rules,
          daily_control_totals, branch_totals, account_balances, balance_breaks]


def run_all(con: duckdb.DuckDBPyConnection) -> list[Check]:
    return [check(con) for check in CHECKS]
