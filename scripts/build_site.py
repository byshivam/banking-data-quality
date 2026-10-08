"""Build site/index.html — the daily data-quality dashboard on GitHub Pages.

Reads reports/quality_report.json (python -m arya_dq.report) and
reports/bug_hunt.json (scripts/bug_hunt.py). Plain HTML + inline SVG, no build step.
"""

from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from arya_dq.pipeline import BUG_CATALOG  # noqa: E402

SITE = ROOT / "site"
esc = html.escape

CSS = """
:root{color-scheme:light;--bg:#f6f6f4;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#77756f;--line:#e4e3df;
--grid:#ecebe7;--series:#2a78d6;--good:#0b7a0b;--goodbg:#e3f3e3;--bad:#b42323;--badbg:#fbe7e7;--warn:#8a5a00;--warnbg:#fdf2d8}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--bg:#121211;--surface:#1a1a19;
--ink:#fff;--ink2:#c3c2b7;--muted:#9a998f;--line:#2e2e2b;--grid:#2a2a27;--series:#3987e5;--good:#5fd35f;--goodbg:#173317;
--bad:#ff8a8a;--badbg:#3b1b1b;--warn:#f2c066;--warnbg:#3a2f15}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 72px}h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:18px;margin:40px 0 6px}.sub{color:var(--ink2);margin:0 0 24px;max-width:760px}.note{color:var(--muted);font-size:13px;margin:0 0 12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.tile .k{color:var(--ink2);font-size:13px}.tile .v{font-size:30px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.2;margin:4px 0}
.tile .s{color:var(--muted);font-size:13px}
.dims{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-top:12px}
.dim{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.dim .k{font-size:13px;color:var(--ink2)}.dim .v{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px}
.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:0}td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill{display:inline-flex;align-items:center;gap:4px;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap}
.ok{color:var(--good);background:var(--goodbg)}.bad{color:var(--bad);background:var(--badbg)}.warn{color:var(--warn);background:var(--warnbg)}
.m{color:var(--muted);font-size:13px}code{font-size:13px}a{color:var(--series)}
.chart{position:relative}.chart svg{display:block;width:100%;height:auto}
.chart .scroll{overflow-x:auto}.chart svg{min-width:640px}.chart .grid line{stroke:var(--grid)}.chart .axis text{fill:var(--muted);font-size:11px}
.chart .bar{fill:var(--series);pointer-events:none}.chart .lbl,.chart .axis{pointer-events:none}.chart .hit{fill:transparent;cursor:default}
.chart .hit:hover + .bar,.chart .bar.on{opacity:.75}.chart .lbl{fill:var(--ink2);font-size:12px;font-weight:600}
.tip{position:absolute;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--line);
border-radius:8px;padding:8px 10px;font-size:13px;box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;white-space:nowrap}
details{margin-top:10px}summary{cursor:pointer;color:var(--ink2);font-size:13px}
"""

TIP_JS = """
(function(){var c=document.querySelector('.chart');if(!c)return;var tip=c.querySelector('.tip');
c.querySelectorAll('.hit').forEach(function(h){
 h.addEventListener('mouseenter',function(){var b=h.nextElementSibling;if(b)b.classList.add('on');
  tip.innerHTML=h.getAttribute('data-tip');tip.style.display='block';});
 h.addEventListener('mousemove',function(e){var r=c.getBoundingClientRect();var x=e.clientX-r.left+12;
  if(x+tip.offsetWidth>r.width)x=e.clientX-r.left-tip.offsetWidth-12;tip.style.left=x+'px';tip.style.top=(e.clientY-r.top-10)+'px';});
 h.addEventListener('mouseleave',function(){var b=h.nextElementSibling;if(b)b.classList.remove('on');tip.style.display='none';});
});})();
"""


def pill(ok: bool, yes: str = "pass", no: str = "fail") -> str:
    return f'<span class="pill {"ok" if ok else "bad"}">{"✓" if ok else "✗"} {esc(yes if ok else no)}</span>'


def daily_chart(days: list[dict]) -> str:
    """Bars of failed rows per day (one series, one hue); worst day labelled directly."""
    w, h, left, right, top, bottom = 960, 260, 44, 12, 22, 30
    pw, ph = w - left - right, h - top - bottom
    peak = max(d["failed"] for d in days) or 1
    step = 10 if peak <= 60 else 20 if peak <= 120 else 50
    ymax = ((peak // step) + 1) * step
    slot = pw / len(days)
    bw = max(6, slot - 6)
    worst = max(days, key=lambda d: d["failed"])
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Failed transaction rows per day, September 2026">']
    parts.append('<g class="grid axis">')
    for v in range(0, ymax + 1, step):
        y = top + ph - ph * v / ymax
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{v}</text>')
    parts.append("</g><g class=\"axis\">")
    for i, d in enumerate(days):
        near_worst = d is not worst and abs(days.index(worst) - i) == 1
        if (i % 3 == 0 and not near_worst) or d is worst:
            x = left + slot * i + slot / 2
            parts.append(f'<text x="{x:.1f}" y="{h - 10}" text-anchor="middle">{int(d["date"][-2:])}</text>')
    parts.append("</g>")
    for i, d in enumerate(days):
        x = left + slot * i + (slot - bw) / 2
        bh = ph * d["failed"] / ymax
        y = top + ph - bh
        r = min(4, bw / 2, bh)
        tip = (f'<strong>{esc(d["date"])}</strong><br>{d["failed"]} failed of {d["rows"]:,} rows'
               f'<br>score {d["score"]}')
        parts.append(f'<rect class="hit" x="{left + slot * i:.1f}" y="{top}" width="{slot:.1f}" height="{ph}" data-tip="{esc(tip)}"/>')
        if bh > 0:
            # rounded top only: path with 4px radius corners at the data end, square at the baseline
            parts.append(
                f'<path class="bar" d="M{x:.1f},{top + ph:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
                f'H{x + bw - r:.1f} Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{top + ph:.1f} Z"/>')
        else:
            parts.append('<g class="bar"></g>')
        if d is worst:
            parts.append(f'<text class="lbl" x="{x + bw / 2:.1f}" y="{y - 7:.1f}" text-anchor="middle">'
                         f'{d["failed"]} · feed incident</text>')
    parts.append("</svg>")
    return '<div class="chart"><div class="scroll">' + "".join(parts) + '</div><div class="tip"></div></div>'


def main() -> None:
    q = json.loads((ROOT / "reports" / "quality_report.json").read_text())
    hunt_path = ROOT / "reports" / "bug_hunt.json"
    hunt = json.loads(hunt_path.read_text()) if hunt_path.exists() else []
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    grading = {g["rule_id"]: g for g in q["grading"]}
    exact = sum(g["precision"] == 1.0 and g["recall"] == 1.0 for g in q["grading"])
    recon = q["reconciliation"]
    recon_ok = sum(c["passed"] for c in recon)
    bug_runs = [r for r in hunt if r["run"].startswith("PIPE-")]
    caught = sum(r["verdict"] == "CAUGHT" for r in bug_runs)
    clean = next((r for r in hunt if r["run"] == "clean"), None)
    worst = min(q["daily"], key=lambda d: d["score"])

    tiles = [
        ("Data quality score", f'{q["score"]["overall"]}', f"of 100 · worst day {worst['date'][-2:]} Sep ({worst['score']})"),
        ("Rules exact vs ground truth", f"{exact}/{len(q['grading'])}", "no misses, no false positives"),
        ("Reconciliation", f"{recon_ok}/{len(recon)}", "source → report checks pass"),
    ]
    if bug_runs:
        tiles.append(("Pipeline bugs caught", f"{caught}/{len(bug_runs)}",
                      f"{clean['passed']}/{clean['total']} tests green on the correct pipeline" if clean else ""))
    tiles_html = "".join(f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div>'
                         f'<div class="s">{esc(s)}</div></div>' for k, v, s in tiles)
    dims_html = "".join(f'<div class="dim"><div class="k">{esc(k)}</div><div class="v">{v}</div></div>'
                        for k, v in q["score"]["dimensions"].items())

    rule_rows = "".join(
        f"<tr><td><code>{r['rule_id']}</code></td><td>{esc(r['dimension'])}</td><td>{esc(r['description'])}"
        f"<div class='m'>{esc(r['table'])}</div></td>"
        f"<td class='n'>{r['failed']:,} / {r['records']:,}</td><td class='n'>{r['pass_rate']}%</td>"
        f"<td>{pill(grading[r['rule_id']]['precision'] == 1.0 and grading[r['rule_id']]['recall'] == 1.0, 'exact', 'off')}"
        f"<div class='m'>{grading[r['rule_id']]['found']} found / {grading[r['rule_id']]['expected']} injected</div></td></tr>"
        for r in q["rules"])

    recon_rows = "".join(
        f"<tr><td><code>{c['check_id']}</code></td><td>{esc(c['name'])}</td><td>{pill(c['passed'])}</td>"
        f"<td class='m'>{esc(c['expected'])}<br>{esc(c['actual'])}</td></tr>" for c in recon)

    gx_rows = "".join(
        f"<tr><td>{esc(r['suite'])}</td><td><code>{esc(r['column'])}</code></td>"
        f"<td>{esc(r['expectation'].replace('expect_column_values_to_', '').replace('_', ' '))}</td>"
        f"<td>{pill(r['success'])}</td><td class='n'>{r['unexpected_count']:,}</td>"
        f"<td class='m'>{esc(', '.join(r['sample']))}</td></tr>" for r in q["expectations"])

    bug_rows = "".join(
        f"<tr><td><code>{r['run']}</code></td><td><strong>{esc(BUG_CATALOG[r['run']]['title'])}</strong>"
        f"<div class='m'>{esc(BUG_CATALOG[r['run']]['impact'])}</div></td>"
        f"<td>{pill(r['verdict'] == 'CAUGHT', 'caught', 'escaped')}</td>"
        f"<td class='m'>{esc(', '.join(r['failed_layers']))}<br>{r['failed']} failing tests</td></tr>"
        for r in bug_runs)

    quarantine = "".join(f"<tr><td><code>{esc(k)}</code></td><td class='n'>{v}</td></tr>" for k, v in q["quarantine"].items())
    daily_table = "".join(f"<tr><td>{d['date']}</td><td class='n'>{d['rows']:,}</td><td class='n'>{d['failed']}</td>"
                          f"<td class='n'>{d['score']}</td></tr>" for d in q["daily"])

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Banking Data Quality</title>
<style>{CSS}</style></head><body><main>
<h1>Arya Bank — data quality &amp; reconciliation</h1>
<p class="sub">Fictional bank, synthetic data: {q['counts']['customers']:,} customers, {q['counts']['accounts']:,} accounts,
{q['counts']['transactions']:,} transactions for September 2026, with known defects injected on purpose.
Checked with SQL rules in DuckDB and Great Expectations, then reconciled against the reporting pipeline. Updated {stamp}.</p>
<div class="tiles">{tiles_html}</div>
<div class="dims">{dims_html}</div>

<h2>Failed transaction rows per day</h2>
<p class="note">Rows breaking a row-level rule (bad currency, non-positive amount, unknown account, duplicate copy).
The 17 Sep spike is the injected feed incident. Rows dated after the cutoff are not shown.</p>
<div class="card">{daily_chart(q['daily'])}
<details><summary>Show as table</summary><div class="wrap"><table><thead><tr><th>Date</th><th>Rows</th><th>Failed</th><th>Score</th></tr></thead>
<tbody>{daily_table}</tbody></table></div></details></div>

<h2>Quality rules (SQL)</h2>
<p class="note">Each rule is graded against the generator's manifest of injected defects.</p>
<div class="card wrap"><table><thead><tr><th>Rule</th><th>Dimension</th><th>Check</th><th>Failed</th><th>Pass rate</th><th>vs ground truth</th></tr></thead>
<tbody>{rule_rows}</tbody></table></div>

<h2>Reconciliation: source → report</h2>
<p class="note">Expected figures are recomputed from the raw extract with independent SQL, then compared with the pipeline output.</p>
<div class="card wrap"><table><thead><tr><th>Check</th><th>What it proves</th><th>Result</th><th>Expected / actual</th></tr></thead>
<tbody>{recon_rows}</tbody></table></div>

<h2>Planted pipeline bugs</h2>
<p class="note">The whole test suite is rerun with one bug switched on at a time. A bug counts as caught when at least one test fails.</p>
<div class="card wrap"><table><thead><tr><th>Bug</th><th>Defect</th><th>Result</th><th>Caught by</th></tr></thead>
<tbody>{bug_rows or '<tr><td colspan="4" class="m">No bug-hunt results in this run.</td></tr>'}</tbody></table></div>

<h2>Great Expectations</h2>
<div class="card wrap"><table><thead><tr><th>Table</th><th>Column</th><th>Expectation</th><th>Result</th><th>Unexpected</th><th>Sample</th></tr></thead>
<tbody>{gx_rows}</tbody></table></div>

<h2>Quarantine</h2>
<p class="note">Rows the pipeline refused to load, with the reason. Nothing is dropped silently.</p>
<div class="card wrap" style="max-width:420px"><table><thead><tr><th>Reason</th><th>Rows</th></tr></thead><tbody>{quarantine}</tbody></table></div>

<p class="m" style="margin-top:32px"><a href="https://github.com/byshivam/banking-data-quality">Source on GitHub</a></p>
</main><script>{TIP_JS}</script></body></html>"""
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(page, encoding="utf-8")
    (SITE / ".nojekyll").write_text("")
    print(f"wrote {SITE / 'index.html'}")


if __name__ == "__main__":
    main()
