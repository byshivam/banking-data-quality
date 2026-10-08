"""Small hand-made extracts, one behaviour per test, so a failure points straight at the cause."""

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from arya_dq import db, pipeline

BRANCHES = [{"branch_code": "ARYB0001", "branch_name": "Arya Bank Pune", "city": "Pune"}]
CUSTOMERS = [{"customer_id": "C000001", "full_name": "Asha Verma", "email": "asha.verma1@example.com",
              "phone": "9876543210", "date_of_birth": "1990-01-01", "city": "Pune", "kyc_status": "VERIFIED",
              "created_at": "2024-01-01"}]
FX = [{"rate_date": "2026-09-05", "currency": "USD", "inr_rate": "88.0000"},
      {"rate_date": "2026-09-30", "currency": "USD", "inr_rate": "90.0000"}]


def account(acct_id="ACC-000001", branch="ARYB0001", opening="1000.00"):
    return {"account_id": acct_id, "customer_id": "C000001", "branch_code": branch, "account_type": "SAVINGS",
            "currency": "INR", "opening_balance": opening, "opened_on": "2024-01-01", "status": "ACTIVE"}


def txn(txn_id, ts, amount, direction="CREDIT", currency="INR", status="SUCCESS", acct="ACC-000001"):
    return {"txn_id": txn_id, "account_id": acct, "txn_ts": ts, "amount": amount, "currency": currency,
            "direction": direction, "channel": "UPI", "counterparty": "Ravi Iyer", "status": status}


def run(tmp_path: Path, transactions, accounts=None):
    accounts = accounts or [account()]
    tables = {
        "branches": BRANCHES, "customers": CUSTOMERS, "accounts": accounts, "transactions": transactions,
        "fx_rates": FX,
        "closing_balances": [{"account_id": a["account_id"], "as_of": "2026-09-30", "closing_balance": "0.00"}
                             for a in accounts],
    }
    for name, rows in tables.items():
        with open(tmp_path / f"{name}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    con = db.connect()
    db.load_raw(con, tmp_path)
    pipeline.run(con)
    return con


def report(con):
    return con.execute(
        "SELECT CAST(report_date AS VARCHAR), branch_code, txn_count, credit_inr, debit_inr "
        "FROM rpt_daily_branch_summary ORDER BY 1, 2").fetchall()


def test_report_day_is_ist_not_utc(tmp_path):
    # 10 Sep 19:00 UTC = 11 Sep 00:30 IST
    con = run(tmp_path, [txn("T1", "2026-09-10T19:00:00Z", "500.00")])
    assert report(con) == [("2026-09-11", "ARYB0001", 1, Decimal("500.00"), Decimal("0.00"))]


def test_fx_uses_the_rate_of_the_transaction_date(tmp_path):
    con = run(tmp_path, [txn("T1", "2026-09-05T06:00:00Z", "100.00", currency="USD")])
    (row,) = report(con)
    assert row[3] == Decimal("8800.00"), "100 USD on 5 Sep at 88.0000"


def test_duplicate_counted_once_and_quarantined(tmp_path):
    t = txn("T1", "2026-09-05T06:00:00Z", "250.00")
    con = run(tmp_path, [t, dict(t)])
    assert report(con)[0][2:4] == (1, Decimal("250.00"))
    assert con.execute("SELECT reject_reason FROM quarantine_transactions").fetchall() == [("DUPLICATE",)]


def test_failed_and_reversed_payments_are_not_money(tmp_path):
    con = run(tmp_path, [
        txn("T1", "2026-09-05T06:00:00Z", "100.00", status="SUCCESS"),
        txn("T2", "2026-09-05T07:00:00Z", "900.00", status="FAILED"),
        txn("T3", "2026-09-05T08:00:00Z", "700.00", status="REVERSED"),
    ])
    assert report(con)[0][2:4] == (1, Decimal("100.00"))


def test_paise_are_kept(tmp_path):
    con = run(tmp_path, [txn("T1", "2026-09-05T06:00:00Z", "100.75"),
                         txn("T2", "2026-09-05T07:00:00Z", "0.99", direction="DEBIT")])
    assert report(con)[0][3:] == (Decimal("100.75"), Decimal("0.99"))


def test_unknown_branch_is_kept_not_dropped(tmp_path):
    con = run(tmp_path, [txn("T1", "2026-09-05T06:00:00Z", "300.00", acct="ACC-000002")],
              accounts=[account(), account("ACC-000002", branch="ARYB9999")])
    assert report(con) == [("2026-09-05", "ARYB9999", 1, Decimal("300.00"), Decimal("0.00"))]
    assert con.execute("SELECT COUNT(*) FROM quarantine_transactions").fetchone()[0] == 0


@pytest.mark.parametrize("bad, reason", [
    ({"currency": "RS"}, "INVALID_CURRENCY"),
    ({"currency": ""}, "INVALID_CURRENCY"),
    ({"amount": "-5.00"}, "NON_POSITIVE_AMOUNT"),
    ({"amount": "0.00"}, "NON_POSITIVE_AMOUNT"),
    ({"account_id": "ACC-999999"}, "UNKNOWN_ACCOUNT"),
    ({"txn_ts": "2026-09-30T18:30:00Z"}, "FUTURE_DATED"),  # exactly 1 Oct 00:00 IST
])
def test_bad_rows_are_quarantined_with_a_reason(tmp_path, bad, reason):
    good = txn("T1", "2026-09-05T06:00:00Z", "10.00")
    con = run(tmp_path, [good, {**txn("T2", "2026-09-06T06:00:00Z", "20.00"), **bad}])
    assert con.execute("SELECT txn_id, reject_reason FROM quarantine_transactions").fetchall() == [("T2", reason)]
    assert con.execute("SELECT txn_id FROM stg_transactions").fetchall() == [("T1",)]


def test_last_second_of_september_is_in_period(tmp_path):
    con = run(tmp_path, [txn("T1", "2026-09-30T18:29:59Z", "10.00")])  # 30 Sep 23:59:59 IST
    assert report(con)[0][0] == "2026-09-30"


def test_balance_difference_is_source_minus_computed(tmp_path):
    con = run(tmp_path, [txn("T1", "2026-09-05T06:00:00Z", "200.00", direction="DEBIT")])
    row = con.execute("SELECT computed_closing, source_closing, difference FROM rpt_account_balances").fetchone()
    assert row == (Decimal("800.00"), Decimal("0.00"), Decimal("-800.00"))
