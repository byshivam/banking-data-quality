# Results

_Last run: 09 Oct 2026 01:30 UTC_

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
