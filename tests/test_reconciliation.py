"""Source-to-report reconciliation on the full month (dirty and clean extracts)."""

import pytest

from arya_dq import reconcile


@pytest.mark.parametrize("check", reconcile.CHECKS, ids=lambda f: f.__name__)
def test_dirty_extract_reconciles(check, dirty_db):
    result = check(dirty_db)
    assert result.passed, f"{result.check_id} {result.name}: expected {result.expected}, got {result.actual}. {result.mismatches[:3]}"


@pytest.mark.parametrize("check", reconcile.CHECKS, ids=lambda f: f.__name__)
def test_clean_extract_reconciles(check, clean_db):
    result = check(clean_db)
    assert result.passed, f"{result.check_id} {result.name}: expected {result.expected}, got {result.actual}. {result.mismatches[:3]}"


def test_clean_extract_has_nothing_in_quarantine(clean_db):
    assert clean_db.execute("SELECT COUNT(*) FROM quarantine_transactions").fetchone()[0] == 0


def test_month_totals_tie_out(dirty_db):
    """Net movement in the daily report = net movement across all account balances."""
    report_net = dirty_db.execute("SELECT SUM(net_inr) FROM rpt_daily_branch_summary").fetchone()[0]
    balances_net = dirty_db.execute("SELECT SUM(credits - debits) FROM rpt_account_balances").fetchone()[0]
    assert report_net == balances_net
