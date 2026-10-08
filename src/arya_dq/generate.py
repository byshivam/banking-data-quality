"""Synthetic Arya Bank source extract, with known data-quality defects injected.

The generator first builds a *clean* month of banking data (September 2026),
then injects defects as described in DEFECTS. Every injected defect is written
to manifest.json with the exact keys affected, so the quality checks can be
graded against ground truth: a rule must flag exactly the injected keys —
nothing missed (recall) and nothing extra (precision).

All data is synthetic: fictional bank, Faker names, example.com emails, and
account / transaction ids that don't look like real bank numbers.

    python -m arya_dq.generate                 # dirty extract -> data/raw
    python -m arya_dq.generate --clean         # same data, no defects
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from faker import Faker

IST = timezone(timedelta(hours=5, minutes=30))
PERIOD_START = datetime(2026, 9, 1, tzinfo=IST)
PERIOD_END = datetime(2026, 10, 1, tzinfo=IST)  # exclusive; extract taken at this cutoff
AS_OF = date(2026, 9, 30)
FOREIGN = {"USD": Decimal("88.20"), "EUR": Decimal("102.60"), "GBP": Decimal("118.40"), "AED": Decimal("24.01")}
CHANNELS = {  # channel: (direction options, min, max) in INR
    "UPI": (("DEBIT", "CREDIT"), 10, 5_000),
    "IMPS": (("DEBIT", "CREDIT"), 500, 50_000),
    "NEFT": (("DEBIT", "CREDIT"), 1_000, 2_00_000),
    "ATM": (("DEBIT",), 100, 10_000),
    "POS": (("DEBIT",), 50, 20_000),
}
CHANNEL_WEIGHTS = [55, 15, 10, 10, 10]

DEFECTS = {
    "DQ-01": ("UNQ-01", "Duplicate transaction rows (same txn_id sent twice)"),
    "DQ-02": ("CMP-01", "Customer missing a mandatory field (email, phone or KYC status)"),
    "DQ-03": ("VAL-01", "Transaction with an invalid currency code"),
    "DQ-04": ("VAL-02", "Transaction with a zero or negative amount"),
    "DQ-05": ("INT-01", "Transaction for an account that does not exist"),
    "DQ-06": ("TML-01", "Transaction dated after the extract cutoff"),
    "DQ-07": ("VAL-03", "Customer email in an invalid format"),
    "DQ-08": ("VAL-04", "Customer phone not a valid 10-digit Indian mobile"),
    "DQ-09": ("CON-01", "Savings account with a negative closing balance"),
    "DQ-10": ("CON-02", "Closing balance does not match opening + transactions"),
    "DQ-11": ("CON-03", "Customer under 18 holding a CURRENT account"),
    "DQ-12": ("INT-02", "Account linked to a branch missing from the branch master"),
}


def money(d: Decimal) -> str:
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def to_inr(amount: Decimal, rate: Decimal) -> Decimal:
    return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Extract:
    branches: list[dict] = field(default_factory=list)
    customers: list[dict] = field(default_factory=list)
    accounts: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    fx_rates: list[dict] = field(default_factory=list)
    closing_balances: list[dict] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower()) or "user"


def build(seed: int = 2026, customers: int = 1000, transactions: int = 50_000, inject: bool = True) -> Extract:
    rng = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)
    ex = Extract()

    # ---- branches
    cities = []
    while len(cities) < 20:
        c = fake.city()
        if c not in cities:
            cities.append(c)
    for i, city in enumerate(cities, start=1):
        ex.branches.append({"branch_code": f"ARYB{i:04d}", "branch_name": f"Arya Bank {city}", "city": city})
    branch_codes = [b["branch_code"] for b in ex.branches]

    # ---- FX: daily INR rate per currency, small random walk, one row per IST date
    rates = dict(FOREIGN)
    day = PERIOD_START.date()
    while day < PERIOD_END.date():
        for cur in FOREIGN:
            rates[cur] = (rates[cur] * Decimal(str(1 + rng.uniform(-0.004, 0.004)))).quantize(Decimal("0.0001"))
            ex.fx_rates.append({"rate_date": day.isoformat(), "currency": cur, "inr_rate": str(rates[cur])})
        day += timedelta(days=1)
    fx = {(r["rate_date"], r["currency"]): Decimal(r["inr_rate"]) for r in ex.fx_rates}

    # ---- customers
    for i in range(1, customers + 1):
        first, last = fake.first_name(), fake.last_name()
        age_days = rng.randint(19 * 365 + 5, 75 * 365)
        ex.customers.append({
            "customer_id": f"C{i:06d}",
            "full_name": f"{first} {last}",
            "email": f"{_slug(first)}.{_slug(last)}{i}@example.com",
            "phone": f"{rng.choice('6789')}{rng.randrange(10**8, 10**9)}",
            "date_of_birth": (AS_OF - timedelta(days=age_days)).isoformat(),
            "city": fake.city(),
            "kyc_status": rng.choices(["VERIFIED", "PENDING"], [95, 5])[0],
            "created_at": (PERIOD_START - timedelta(days=rng.randint(30, 3000))).date().isoformat(),
        })

    # ---- accounts (opening balance = balance at the start of the period)
    n = 0
    opening: dict[str, Decimal] = {}
    for cust in ex.customers:
        for _ in range(rng.choices([1, 2], [65, 35])[0]):
            n += 1
            acct_id = f"ACC-{n:06d}"
            bal = Decimal(rng.randint(5_000_00, 5_00_000_00)) / 100
            opening[acct_id] = bal
            ex.accounts.append({
                "account_id": acct_id,
                "customer_id": cust["customer_id"],
                "branch_code": rng.choice(branch_codes),
                "account_type": rng.choices(["SAVINGS", "CURRENT"], [80, 20])[0],
                "currency": "INR",
                "opening_balance": money(bal),
                "opened_on": cust["created_at"],
                "status": "ACTIVE",
            })
    account_ids = list(opening)

    # ---- transactions: generated in time order so a debit never overdraws
    seconds = int((PERIOD_END - PERIOD_START).total_seconds())
    stamps = sorted(rng.randrange(seconds) for _ in range(transactions))
    names = [fake.name() for _ in range(1500)]
    balance = dict(opening)
    for i, offset in enumerate(stamps, start=1):
        ts_ist = PERIOD_START + timedelta(seconds=offset)
        acct = rng.choice(account_ids)
        status = rng.choices(["SUCCESS", "FAILED", "REVERSED"], [95, 4, 1])[0]
        if rng.random() < 0.03:  # inward foreign remittance
            cur = rng.choice(list(FOREIGN))
            amount = Decimal(rng.randint(50_00, 3_000_00)) / 100
            channel, direction = "SWIFT", "CREDIT"
            inr = to_inr(amount, fx[(ts_ist.date().isoformat(), cur)])
        else:
            cur = "INR"
            channel = rng.choices(list(CHANNELS), CHANNEL_WEIGHTS)[0]
            dirs, lo, hi = CHANNELS[channel]
            direction = rng.choice(dirs)
            amount = Decimal(rng.randint(lo * 100, hi * 100)) / 100
            inr = amount
        if status == "SUCCESS":
            if direction == "DEBIT" and inr > balance[acct]:
                status = "FAILED"  # insufficient funds
            else:
                balance[acct] += inr if direction == "CREDIT" else -inr
        ex.transactions.append({
            "txn_id": f"T{i:08d}",
            "account_id": acct,
            "txn_ts": ts_ist.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "amount": money(amount),
            "currency": cur,
            "direction": direction,
            "channel": channel,
            "counterparty": rng.choice(names),
            "status": status,
        })

    ex.closing_balances = [
        {"account_id": a, "as_of": AS_OF.isoformat(), "closing_balance": money(balance[a])} for a in account_ids
    ]
    ex.manifest = {
        "seed": seed,
        "as_of": AS_OF.isoformat(),
        "counts": {},
        "defects": {d: {"rule": r, "description": desc, "keys": []} for d, (r, desc) in DEFECTS.items()},
    }
    if inject:
        _inject(ex, rng)
    ex.manifest["counts"] = {
        "branches": len(ex.branches), "customers": len(ex.customers), "accounts": len(ex.accounts),
        "transactions": len(ex.transactions), "fx_rates": len(ex.fx_rates),
    }
    for d in ex.manifest["defects"].values():
        d["keys"] = sorted(set(d["keys"]))
    return ex


def _inject(ex: Extract, rng: random.Random) -> None:
    """Add known defects. Bad transactions are *extra* rows, so the source
    closing balances (built from clean rows) stay right for a correct pipeline."""
    defects = ex.manifest["defects"]
    txns = ex.transactions
    seconds = int((PERIOD_END - PERIOD_START).total_seconds())
    next_id = len(txns) + 1

    def extra_txn(**over) -> dict:
        nonlocal next_id
        base = dict(rng.choice(txns[:50_000]))
        ts = PERIOD_START + timedelta(seconds=rng.randrange(seconds))
        base.update(txn_id=f"T{next_id:08d}", txn_ts=ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    currency="INR", status="SUCCESS")
        base.update(over)
        next_id += 1
        txns.append(base)
        return base

    # DQ-01 duplicates: exact re-sends of existing successful rows
    for row in rng.sample([t for t in txns if t["status"] == "SUCCESS"], 150):
        txns.append(dict(row))
        defects["DQ-01"]["keys"].append(row["txn_id"])
    # DQ-03 invalid currency — one upstream incident: on 17 Sep, 10:00-12:00 IST,
    # a channel feed sent 60 rows with broken currency codes (shows as a dip in the daily score)
    incident = datetime(2026, 9, 17, 10, 0, tzinfo=IST)
    for bad in ["INRR", "usd", "", "RS", "XYZ"] * 12:
        ts = incident + timedelta(seconds=rng.randrange(2 * 3600))
        row = extra_txn(currency=bad, txn_ts=ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        defects["DQ-03"]["keys"].append(row["txn_id"])
    # DQ-04 zero / negative amounts
    for _ in range(30):
        amt = rng.choice(["0.00", f"-{rng.randint(1, 5000)}.{rng.randint(0, 99):02d}"])
        defects["DQ-04"]["keys"].append(extra_txn(amount=amt)["txn_id"])
    # DQ-05 orphan account
    for _ in range(25):
        defects["DQ-05"]["keys"].append(extra_txn(account_id=f"ACC-{rng.randint(900000, 999999)}")["txn_id"])
    # DQ-06 future-dated (after the 30 Sep IST cutoff)
    for _ in range(20):
        ts = PERIOD_END + timedelta(hours=rng.randint(1, 240))
        defects["DQ-06"]["keys"].append(extra_txn(txn_ts=ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))["txn_id"])
    rng.shuffle(txns)

    customers = ex.customers
    picked = rng.sample(customers, 40 + 25 + 20)
    # DQ-02 missing mandatory fields
    for i, c in enumerate(picked[:40]):
        c[["email", "phone", "kyc_status"][i % 3]] = ""
        defects["DQ-02"]["keys"].append(c["customer_id"])
    # DQ-07 invalid email
    for c in picked[40:65]:
        c["email"] = rng.choice(["{}@", "{}.example.com", "{} {}@example.com", "{}@@example.com"]).format(
            c["full_name"].split()[0].lower(), "x")
        defects["DQ-07"]["keys"].append(c["customer_id"])
    # DQ-08 invalid phone
    for c in picked[65:85]:
        c["phone"] = rng.choice(["12345", "0123456789", "+91-98", "98765432101", "5987654321"])
        defects["DQ-08"]["keys"].append(c["customer_id"])

    # DQ-11 minors holding a CURRENT account
    by_cust = {}
    for a in ex.accounts:
        by_cust.setdefault(a["customer_id"], []).append(a)
    current_holders = [c for c in customers if any(a["account_type"] == "CURRENT" for a in by_cust[c["customer_id"]])
                       and c["customer_id"] not in defects["DQ-02"]["keys"]]
    for c in rng.sample(current_holders, 8):
        c["date_of_birth"] = (AS_OF - timedelta(days=rng.randint(14 * 365, 17 * 365))).isoformat()
        defects["DQ-11"]["keys"].append(c["customer_id"])

    # DQ-12 account linked to an unknown branch (its transactions are still valid)
    with_txns = {t["account_id"] for t in txns}
    for a in rng.sample([a for a in ex.accounts if a["account_id"] in with_txns], 5):
        a["branch_code"] = "ARYB9999"
        defects["DQ-12"]["keys"].append(a["account_id"])

    # DQ-09 savings with negative closing balance; DQ-10 closing balance mismatch.
    # A negative closing balance also breaks opening + transactions, so DQ-09 keys
    # appear in CON-02 too; the manifest records that overlap explicitly.
    savings = {a["account_id"] for a in ex.accounts if a["account_type"] == "SAVINGS"}
    chosen = rng.sample(ex.closing_balances, 40)
    negatives = [r for r in chosen if r["account_id"] in savings][:10]
    mismatches = [r for r in chosen if r not in negatives][:15]
    for row in negatives:
        row["closing_balance"] = money(-Decimal(rng.randint(100, 50_000_00)) / 100)
        defects["DQ-09"]["keys"].append(row["account_id"])
    for row in mismatches:
        delta = Decimal(rng.randint(1, 10_000_00)) / 100
        current = Decimal(row["closing_balance"])
        # push it the wrong way, but never below zero (that would be DQ-09, not DQ-10)
        row["closing_balance"] = money(current - delta if current - delta >= 0 and rng.random() < 0.5 else current + delta)
        defects["DQ-10"]["keys"].append(row["account_id"])
    defects["DQ-10"]["also_flagged_by_rule"] = "DQ-09 keys are also expected in CON-02"


def write(ex: Extract, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("branches", "customers", "accounts", "transactions", "fx_rates", "closing_balances"):
        rows = getattr(ex, name)
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    (out_dir / "manifest.json").write_text(json.dumps(ex.manifest, indent=2), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default="data/raw")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--clean", action="store_true", help="skip defect injection")
    args = p.parse_args()
    ex = build(seed=args.seed, inject=not args.clean)
    write(ex, Path(args.out))
    print(f"wrote {ex.manifest['counts']} to {args.out}")


if __name__ == "__main__":
    main()
