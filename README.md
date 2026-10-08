# banking-data-quality

Data quality and reconciliation suite for a month of synthetic **Arya Bank** data, with defects injected on purpose so every check can be graded against ground truth.

Same fictional bank as [payments-api-testing](https://github.com/byshivam/payments-api-testing) and [banking-rag-eval](https://github.com/byshivam/banking-rag-eval). All data is generated: Faker names, `example.com` emails, `ACC-` account ids. Nothing real.

**Live dashboard:** https://byshivam.github.io/banking-data-quality/

| Piece | Tooling | What it does |
|---|---|---|
| Synthetic extract | Python + Faker | 1,000 customers, ~1,370 accounts, 50,000 September transactions, FX rates, closing balances; then injects 12 defect types and writes a manifest of exactly which records are bad |
| Quality rules | SQL on DuckDB | 12 rules across six dimensions: completeness, uniqueness, validity, integrity, timeliness, consistency |
| Column checks | Great Expectations 1.x | Not-null, unique, in-set, regex and range expectations on the raw tables |
| Pipeline under test | SQL on DuckDB | raw → staging + quarantine → daily branch report + account balances, with 6 switchable planted bugs |
| Reconciliation | Independent SQL | Recomputes every figure from the raw extract and compares it with the report |
| Tests & scoring | pytest | 68 tests; quality score per dimension and per day |

## Results

<!-- RESULTS:START -->
_Last run: 08 Oct 2026 12:06 UTC_

**Data quality score:** 98.86 / 100 (worst day 2026-09-17: 96.18).  
**Quality rules:** 12/12 flag exactly the injected defects (no misses, no false positives).  
**Reconciliation (source → report):** 7/7 checks pass.  
**Test suite on the correct pipeline:** 68/68 pass.  
**Planted pipeline bugs caught:** 6/6.  

| Dimension | Score |
|---|---|
| Completeness | 96.0 |
| Uniqueness | 99.7 |
| Validity | 98.83 |
| Integrity | 99.79 |
| Timeliness | 99.96 |
| Consistency | 98.88 |

| Bug | What it does | Result | Caught by | Failing tests |
|---|---|---|---|---|
| PIPE-01 | Duplicates not removed | ✅ caught | Unit, Reconciliation | 7 |
| PIPE-02 | FX uses the latest rate for every day | ✅ caught | Unit, Reconciliation | 9 |
| PIPE-03 | Report date taken in UTC, not IST | ✅ caught | Unit, Reconciliation | 9 |
| PIPE-04 | Inner join to branch master | ✅ caught | Unit, Reconciliation | 7 |
| PIPE-05 | Failed and reversed payments counted | ✅ caught | Unit, Reconciliation | 9 |
| PIPE-06 | Paise dropped | ✅ caught | Unit, Reconciliation | 9 |

<details><summary>Which tests caught which bug</summary>

**PIPE-01 — Duplicates not removed.** A re-sent transaction is counted twice in the daily totals.

- `Unit` test_duplicate_counted_once_and_quarantined
- `Reconciliation` test_dirty_extract_reconciles[valid_rows_loaded_once]
- `Reconciliation` test_dirty_extract_reconciles[quarantine_matches_rules]
- `Reconciliation` test_dirty_extract_reconciles[daily_control_totals]
- `Reconciliation` test_dirty_extract_reconciles[branch_totals]
- `Reconciliation` test_dirty_extract_reconciles[account_balances]
- `Reconciliation` test_dirty_extract_reconciles[balance_breaks]

**PIPE-02 — FX uses the latest rate for every day.** Foreign remittances are converted at the 30 Sep rate instead of the rate on their own date.

- `Unit` test_fx_uses_the_rate_of_the_transaction_date
- `Reconciliation` test_dirty_extract_reconciles[daily_control_totals]
- `Reconciliation` test_dirty_extract_reconciles[branch_totals]
- `Reconciliation` test_dirty_extract_reconciles[account_balances]
- `Reconciliation` test_dirty_extract_reconciles[balance_breaks]
- `Reconciliation` test_clean_extract_reconciles[daily_control_totals]
- `Reconciliation` test_clean_extract_reconciles[branch_totals]
- `Reconciliation` test_clean_extract_reconciles[account_balances]
- … and 1 more

**PIPE-03 — Report date taken in UTC, not IST.** Payments made between 00:00 and 05:30 IST land on the previous day's report.

- `Unit` test_report_day_is_ist_not_utc
- `Reconciliation` test_dirty_extract_reconciles[daily_control_totals]
- `Reconciliation` test_dirty_extract_reconciles[branch_totals]
- `Reconciliation` test_dirty_extract_reconciles[account_balances]
- `Reconciliation` test_dirty_extract_reconciles[balance_breaks]
- `Reconciliation` test_clean_extract_reconciles[daily_control_totals]
- `Reconciliation` test_clean_extract_reconciles[branch_totals]
- `Reconciliation` test_clean_extract_reconciles[account_balances]
- … and 1 more

**PIPE-04 — Inner join to branch master.** Transactions of accounts with an unknown branch vanish without being quarantined.

- `Unit` test_unknown_branch_is_kept_not_dropped
- `Reconciliation` test_dirty_extract_reconciles[row_accounting]
- `Reconciliation` test_dirty_extract_reconciles[valid_rows_loaded_once]
- `Reconciliation` test_dirty_extract_reconciles[daily_control_totals]
- `Reconciliation` test_dirty_extract_reconciles[branch_totals]
- `Reconciliation` test_dirty_extract_reconciles[account_balances]
- `Reconciliation` test_dirty_extract_reconciles[balance_breaks]

**PIPE-05 — Failed and reversed payments counted.** Totals include money that never moved.

- `Unit` test_failed_and_reversed_payments_are_not_money
- `Reconciliation` test_dirty_extract_reconciles[daily_control_totals]
- `Reconciliation` test_dirty_extract_reconciles[branch_totals]
- `Reconciliation` test_dirty_extract_reconciles[account_balances]
- `Reconciliation` test_dirty_extract_reconciles[balance_breaks]
- `Reconciliation` test_clean_extract_reconciles[daily_control_totals]
- `Reconciliation` test_clean_extract_reconciles[branch_totals]
- `Reconciliation` test_clean_extract_reconciles[account_balances]
- … and 1 more

**PIPE-06 — Paise dropped.** Amounts are floored to whole rupees before totalling.

- `Unit` test_paise_are_kept
- `Reconciliation` test_dirty_extract_reconciles[daily_control_totals]
- `Reconciliation` test_dirty_extract_reconciles[branch_totals]
- `Reconciliation` test_dirty_extract_reconciles[account_balances]
- `Reconciliation` test_dirty_extract_reconciles[balance_breaks]
- `Reconciliation` test_clean_extract_reconciles[daily_control_totals]
- `Reconciliation` test_clean_extract_reconciles[branch_totals]
- `Reconciliation` test_clean_extract_reconciles[account_balances]
- … and 1 more

</details>
<!-- RESULTS:END -->

## Two kinds of planted problems

**1. Bad data in the source (the checks must find it).** The generator builds a clean month first, then injects these and records every affected key in `manifest.json`:

| Defect | Rule | Example |
|---|---|---|
| DQ-01 | UNQ-01 | 150 transactions sent twice |
| DQ-02 | CMP-01 | Customer with no email, phone or KYC status |
| DQ-03 | VAL-01 | 60 rows with currency `RS`, `usd`, `INRR` or blank, all in one feed incident on 17 Sep |
| DQ-04 | VAL-02 | Amount `0.00` or negative |
| DQ-05 | INT-01 | Transaction for an account that doesn't exist |
| DQ-06 | TML-01 | Transaction dated after the 30 Sep cutoff |
| DQ-07 / 08 | VAL-03 / 04 | Broken email; phone not a 10-digit mobile |
| DQ-09 | CON-01 | Savings account with a negative closing balance |
| DQ-10 | CON-02 | Closing balance ≠ opening + credits − debits |
| DQ-11 | CON-03 | Customer under 18 holding a CURRENT account |
| DQ-12 | INT-02 | Account linked to a branch missing from the branch master |

A rule passes only if it flags **exactly** the injected keys: nothing missed (recall) and nothing extra (precision). Every rule must also stay silent on the clean version of the same data.

**2. Bugs in the pipeline (the tests must catch them).** The reporting pipeline ships with six realistic ETL bugs behind `PIPELINE_BUGS`:

| Bug | What goes wrong |
|---|---|
| PIPE-01 | Duplicates not removed; re-sent payments counted twice |
| PIPE-02 | FX uses the latest rate for every day |
| PIPE-03 | Report date taken in UTC, so 00:00–05:30 IST payments land on the wrong day |
| PIPE-04 | Inner join to branch master; transactions of unknown-branch accounts vanish |
| PIPE-05 | Failed and reversed payments counted as money |
| PIPE-06 | Paise dropped before totalling |

`scripts/bug_hunt.py` reruns the full pytest suite once per bug. A bug that no test catches "escapes" and CI goes red.

One finding worth noting: PIPE-01 and PIPE-04 pass every reconciliation check on the **clean** extract, because clean data has no duplicates and no unknown branches. Only the dirty extract (and a targeted unit test) exposes them. Testing a pipeline on tidy data alone would have shipped both bugs.

## Reconciliation checks

| Check | What it proves |
|---|---|
| REC-01 | Every raw row is either loaded or quarantined (nothing dropped silently) |
| REC-02 | Each valid transaction is loaded exactly once |
| REC-03 | Quarantine counts per reason match the quality rules |
| REC-04 | Daily control totals (count, credits, debits) match the source |
| REC-05 | Every branch-day row matches the source |
| REC-06 | Computed closing balance per account matches the ledger |
| REC-07 | Balance breaks in the report are exactly the known source breaks |

Expected figures come from the QA team's own SQL (`rules.valid_transactions_sql`), not from pipeline code, so a pipeline bug can't hide by being repeated in the check.

## Project layout

```
src/arya_dq/
  generate.py       synthetic extract + defect injection + manifest
  db.py             loads raw CSVs into DuckDB as text (bad values survive the load)
  rules.py          12 SQL quality rules + the independent "valid transaction" oracle
  expectations.py   Great Expectations suites
  pipeline.py       the ETL under test, with planted bugs
  reconcile.py      source-to-report reconciliation
  score.py          quality score per rule, dimension and day; grading vs manifest
  report.py         runs everything -> reports/quality_report.json
tests/              pytest: generator, rules, GX, pipeline units, reconciliation, score
scripts/bug_hunt.py    clean + per-bug test runs, README results
scripts/build_site.py  dashboard for GitHub Pages
```

## Run it locally

```bash
pip install -e ".[test]"
python -m arya_dq.generate          # data/raw/*.csv + manifest.json
python -m arya_dq.report            # reports/quality_report.json
pytest                              # 68 tests on the correct pipeline
PIPELINE_BUGS=PIPE-03 pytest        # watch a planted bug get caught
python scripts/bug_hunt.py          # all runs, about 90 seconds
python scripts/build_site.py        # site/index.html
```

## Design choices

- **Raw data is loaded as text.** Casting on load would turn `"RS"` or `""` into nulls or errors before any check sees them.
- **SQL for rules, Great Expectations for columns.** GX is good at one-column checks; cross-table rules (orphans, balances) read better as SQL.
- **Ground truth from the generator.** Instead of "the check found 60 rows", the tests prove "the check found exactly the 60 rows we broke".
- **Bad rows go to quarantine with a reason.** REC-01 holds the pipeline to "loaded + quarantined = received".

## CI

`.github/workflows/quality.yml` runs on every push and PR, and daily at 03:17 IST: generate data, run the checks and the pipeline, run the bug hunt, publish the dashboard to GitHub Pages. Scheduled and manual runs also refresh the Results section above.
