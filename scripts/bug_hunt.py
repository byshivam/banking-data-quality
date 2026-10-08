"""Run the pytest suite against the pipeline with each planted bug switched on.

    python scripts/bug_hunt.py --update-readme

  * clean pipeline -> every test passes (no false alarms)
  * each PIPE bug  -> at least one test fails (the bug is caught)
  * all bugs on    -> shows how loud the suite gets on a bad release

A bug that no test catches "escaped" and the script exits non-zero.
Writes reports/bug_hunt.json, reports/BUG_HUNT.md and the README results block.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from arya_dq.pipeline import BUG_CATALOG  # noqa: E402

WORK, REPORTS, README = ROOT / "work", ROOT / "reports", ROOT / "README.md"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
LAYERS = {
    "test_pipeline_units": "Unit",
    "test_reconciliation": "Reconciliation",
    "test_rules": "Rules",
    "test_expectations": "Great Expectations",
    "test_score": "Score",
    "test_generator": "Generator",
}


def run_pytest(run: str, bugs: str) -> dict:
    WORK.mkdir(exist_ok=True)
    xml_path = WORK / f"{run}.xml"
    xml_path.unlink(missing_ok=True)
    started = time.time()
    with open(WORK / f"{run}.log", "w") as log:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}"],
            cwd=ROOT, env={**os.environ, "PIPELINE_BUGS": bugs}, stdout=log, stderr=subprocess.STDOUT,
        )
    tests = []
    if xml_path.exists():
        for case in ET.parse(xml_path).getroot().iter("testcase"):
            module = case.get("classname", "").split(".")[-1]
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            tests.append({
                "name": case.get("name"),
                "module": module,
                "layer": LAYERS.get(module, module),
                "status": "skipped" if case.find("skipped") is not None else ("failed" if bad is not None else "passed"),
                "message": ((bad.get("message") or "")[:200] if bad is not None else ""),
            })
    failed = [t for t in tests if t["status"] == "failed"]
    order = list(dict.fromkeys(LAYERS.values()))
    return {
        "run": run,
        "bugs": bugs,
        "exit_code": proc.returncode,
        "duration_s": round(time.time() - started, 1),
        "total": len(tests),
        "passed": sum(t["status"] == "passed" for t in tests),
        "failed": len(failed),
        "failed_tests": failed,
        "failed_layers": [l for l in order if any(t["layer"] == l for t in failed)],
    }


def verdict(r: dict) -> str:
    if r["total"] == 0 or r["exit_code"] not in (0, 1):
        return "ERROR"
    if r["run"] == "clean":
        return "PASS" if r["failed"] == 0 else "FALSE ALARM"
    return "CAUGHT" if r["failed"] else "ESCAPED"


def markdown(results: list[dict], quality: dict | None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    by = {r["run"]: r for r in results}
    bug_runs = [r for r in results if r["run"].startswith("PIPE-")]
    out = [f"_Last run: {stamp}_\n"]
    if quality:
        s = quality["score"]
        worst = min(quality["daily"], key=lambda d: d["score"])
        recon = quality["reconciliation"]
        grading = quality["grading"]
        exact = sum(g["precision"] == 1.0 and g["recall"] == 1.0 for g in grading)
        out.append(f"**Data quality score:** {s['overall']} / 100 "
                   f"(worst day {worst['date']}: {worst['score']}).  ")
        out.append(f"**Quality rules:** {exact}/{len(grading)} flag exactly the injected defects "
                   f"(no misses, no false positives).  ")
        out.append(f"**Reconciliation (source → report):** {sum(c['passed'] for c in recon)}/{len(recon)} checks pass.  ")
    if "clean" in by:
        c = by["clean"]
        out.append(f"**Test suite on the correct pipeline:** {c['passed']}/{c['total']} pass.  ")
    if bug_runs:
        caught = sum(verdict(r) == "CAUGHT" for r in bug_runs)
        out.append(f"**Planted pipeline bugs caught:** {caught}/{len(bug_runs)}.  ")
    out.append("")
    if quality:
        out.append("| Dimension | Score |")
        out.append("|---|---|")
        for dim, v in quality["score"]["dimensions"].items():
            out.append(f"| {dim} | {v} |")
        out.append("")
    if bug_runs:
        out.append("| Bug | What it does | Result | Caught by | Failing tests |")
        out.append("|---|---|---|---|---|")
        for r in bug_runs:
            v = verdict(r)
            mark = "✅ caught" if v == "CAUGHT" else ("❌ escaped" if v == "ESCAPED" else "⚠️ error")
            out.append(f"| {r['run']} | {BUG_CATALOG[r['run']]['title']} | {mark} | "
                       f"{', '.join(r['failed_layers']) or '—'} | {r['failed']} |")
        out.append("")
        out.append("<details><summary>Which tests caught which bug</summary>\n")
        for r in bug_runs:
            out.append(f"**{r['run']} — {BUG_CATALOG[r['run']]['title']}.** {BUG_CATALOG[r['run']]['impact']}\n")
            for t in r["failed_tests"][:8]:
                out.append(f"- `{t['layer']}` {t['name']}")
            if len(r["failed_tests"]) > 8:
                out.append(f"- … and {len(r['failed_tests']) - 8} more")
            out.append("")
        out.append("</details>")
    return "\n".join(out)


def update_readme(block: str) -> None:
    text = README.read_text(encoding="utf-8")
    if START in text and END in text:
        head, rest = text.split(START, 1)
        _, tail = rest.split(END, 1)
        README.write_text(f"{head}{START}\n{block}\n{END}{tail}", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runs", default="clean," + ",".join(BUG_CATALOG) + ",all")
    p.add_argument("--update-readme", action="store_true")
    args = p.parse_args()

    REPORTS.mkdir(exist_ok=True)
    results = []
    for run in [r.strip() for r in args.runs.split(",") if r.strip()]:
        bugs = "none" if run == "clean" else run
        print(f"▶ {run} (PIPELINE_BUGS={bugs})", flush=True)
        r = run_pytest(run, bugs)
        r["verdict"] = verdict(r)
        print(f"  {r['verdict']}: {r['passed']} passed, {r['failed']} failed of {r['total']} "
              f"in {r['duration_s']}s {r['failed_layers'] or ''}", flush=True)
        results.append(r)

    quality_path = REPORTS / "quality_report.json"
    quality = json.loads(quality_path.read_text()) if quality_path.exists() else None

    (REPORTS / "bug_hunt.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    block = markdown(results, quality)
    (REPORTS / "BUG_HUNT.md").write_text("# Results\n\n" + block + "\n", encoding="utf-8")
    if args.update_readme:
        update_readme(block)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("## Data quality results\n\n" + block + "\n")

    bad = [r["run"] for r in results if r["verdict"] in ("ERROR", "FALSE ALARM", "ESCAPED")]
    if bad:
        print(f"✗ Problems in: {', '.join(bad)}")
        return 1
    print("✓ Correct pipeline passes and every planted bug was caught.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
