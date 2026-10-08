"""Quality score per rule, per dimension and per day, plus grading of the
rules against the generator's ground-truth manifest."""

from __future__ import annotations

import duckdb

from .rules import RULES, table_rows

DIMENSIONS = ["Completeness", "Uniqueness", "Validity", "Integrity", "Timeliness", "Consistency"]
TXN_ROW_RULES = ("VAL-01", "VAL-02", "INT-01")


def expected_keys(manifest: dict) -> dict[str, set[str]]:
    """Rule id -> keys the rule should flag, from the injected defects."""
    out: dict[str, set[str]] = {r.rule_id: set() for r in RULES}
    for defect in manifest["defects"].values():
        out[defect["rule"]].update(defect["keys"])
    # A negative savings balance (CON-01) also breaks opening + transactions (CON-02).
    out["CON-02"] |= out["CON-01"]
    return out


def grade(findings: dict[str, list[str]], manifest: dict) -> list[dict]:
    exp = expected_keys(manifest)
    rows = []
    for r in RULES:
        found, want = set(findings[r.rule_id]), exp[r.rule_id]
        hits = len(found & want)
        rows.append({
            "rule_id": r.rule_id,
            "expected": len(want),
            "found": len(found),
            "missed": sorted(want - found)[:10],
            "false_positives": sorted(found - want)[:10],
            "precision": round(hits / len(found), 4) if found else 1.0,
            "recall": round(hits / len(want), 4) if want else 1.0,
        })
    return rows


def rule_scores(con: duckdb.DuckDBPyConnection, findings: dict[str, list[str]]) -> list[dict]:
    rows = []
    for r in RULES:
        total = table_rows(con, r.table)
        bad = len(findings[r.rule_id])
        rows.append({
            "rule_id": r.rule_id, "dimension": r.dimension, "table": r.table, "description": r.description,
            "records": total, "failed": bad, "pass_rate": round(100 * (1 - bad / total), 2) if total else 100.0,
        })
    return rows


def dimension_scores(rule_rows: list[dict]) -> dict[str, float]:
    out = {}
    for dim in DIMENSIONS:
        rates = [r["pass_rate"] for r in rule_rows if r["dimension"] == dim]
        out[dim] = round(sum(rates) / len(rates), 2) if rates else 100.0
    return out


def overall_score(dims: dict[str, float]) -> float:
    return round(sum(dims.values()) / len(dims), 2)


def daily_scores(con: duckdb.DuckDBPyConnection, findings: dict[str, list[str]]) -> list[dict]:
    """Share of each day's transaction rows that pass every row-level rule.

    Second and later copies of a duplicated txn_id count as failed rows.
    Rows dated after the cutoff have no report day in the period and are left out.
    """
    bad_ids = sorted({k for rid in TXN_ROW_RULES for k in findings[rid]})
    con.execute("CREATE OR REPLACE TEMP TABLE _bad_ids (txn_id VARCHAR)")
    if bad_ids:
        con.executemany("INSERT INTO _bad_ids VALUES (?)", [(k,) for k in bad_ids])
    rows = con.execute("""
        WITH t AS (
            SELECT txn_id,
                   CAST(TRY_CAST(replace(txn_ts, 'Z', '') AS TIMESTAMP) + INTERVAL 330 MINUTE AS DATE) AS day,
                   row_number() OVER (PARTITION BY txn_id ORDER BY txn_id) AS copy_no
            FROM raw_transactions
        )
        SELECT day, COUNT(*) AS rows,
               SUM(CASE WHEN copy_no > 1 OR txn_id IN (SELECT txn_id FROM _bad_ids) THEN 1 ELSE 0 END) AS failed
        FROM t WHERE day <= DATE '2026-09-30'
        GROUP BY day ORDER BY day
    """).fetchall()
    return [{"date": d.isoformat(), "rows": n, "failed": int(f),
             "score": round(100 * (1 - f / n), 2)} for d, n, f in rows]
