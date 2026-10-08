"""Run the whole quality check and write reports/quality_report.json.

    python -m arya_dq.report --raw data/raw

Steps: load the raw extract into DuckDB, run the SQL rules and the Great
Expectations suites, run the pipeline, reconcile its output against the
source, and compute the quality score.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import db, expectations, pipeline, reconcile, rules, score


def build_report(raw_dir: str | Path, db_path: str | Path = ":memory:") -> dict:
    raw_dir = Path(raw_dir)
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    con = db.connect(db_path)
    db.load_raw(con, raw_dir)

    findings = rules.run_all(con)
    rule_rows = score.rule_scores(con, findings)
    dims = score.dimension_scores(rule_rows)
    gx_results = expectations.run(raw_dir)

    bugs = pipeline.active_bugs()
    pipeline.run(con, bugs)
    checks = reconcile.run_all(con)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": manifest["as_of"],
        "counts": manifest["counts"],
        "pipeline_bugs": sorted(bugs),
        "score": {"overall": score.overall_score(dims), "dimensions": dims},
        "rules": rule_rows,
        "grading": score.grade(findings, manifest),
        "daily": score.daily_scores(con, findings),
        "expectations": [r.to_dict() for r in gx_results],
        "reconciliation": [c.to_dict() for c in checks],
        "quarantine": dict(con.execute(
            "SELECT reject_reason, COUNT(*) FROM quarantine_transactions GROUP BY 1 ORDER BY 1").fetchall()),
        "report_rows": con.execute("SELECT COUNT(*) FROM rpt_daily_branch_summary").fetchone()[0],
    }
    con.close()
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Run all data-quality checks")
    p.add_argument("--raw", default="data/raw")
    p.add_argument("--db", default="data/arya_bank.duckdb")
    p.add_argument("--out", default="reports/quality_report.json")
    args = p.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    Path(args.db).unlink(missing_ok=True)
    report = build_report(args.raw, args.db)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    recon_ok = sum(c["passed"] for c in report["reconciliation"])
    print(f"Quality score {report['score']['overall']} | "
          f"reconciliation {recon_ok}/{len(report['reconciliation'])} passed | written to {args.out}")


if __name__ == "__main__":
    main()
