"""Great Expectations suites for the raw extract (column-level checks).

GX covers what it is good at — one column at a time: not null, unique, in a
set, matches a pattern, within a range. Rules that need several tables
(orphan accounts, balance consistency) stay in SQL (rules.py).
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

os.environ.setdefault("GX_ANALYTICS_ENABLED", "False")
warnings.filterwarnings("ignore", module="great_expectations")
logging.getLogger("great_expectations").setLevel(logging.ERROR)

import great_expectations as gx  # noqa: E402
import great_expectations.expectations as gxe  # noqa: E402

CUTOFF_UTC = pd.Timestamp("2026-09-30 18:30:00")
LAST_ALLOWED_UTC = CUTOFF_UTC - pd.Timedelta(seconds=1)


@dataclass
class ExpectationResult:
    suite: str
    expectation: str
    column: str
    success: bool
    unexpected_count: int
    element_count: int
    sample: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _frames(raw_dir: Path) -> dict[str, pd.DataFrame]:
    read = lambda name: pd.read_csv(raw_dir / f"{name}.csv", dtype=str)  # noqa: E731  ("" -> NaN)
    tx = read("transactions")
    tx["amount_num"] = pd.to_numeric(tx["amount"], errors="coerce")
    tx["txn_ts_utc"] = pd.to_datetime(tx["txn_ts"].str.replace("Z", "", regex=False), errors="coerce")
    return {
        "transactions": tx,
        "customers": read("customers"),
        "accounts": read("accounts"),
        "branches": read("branches"),
        "fx_rates": read("fx_rates"),
    }


def _suites(frames: dict[str, pd.DataFrame]) -> dict[str, list]:
    currencies = ["INR", *sorted(frames["fx_rates"]["currency"].unique())]
    branches = sorted(frames["branches"]["branch_code"].unique())
    return {
        "transactions": [
            gxe.ExpectColumnValuesToNotBeNull(column="txn_id"),
            gxe.ExpectColumnValuesToBeUnique(column="txn_id"),
            gxe.ExpectColumnValuesToMatchRegex(column="account_id", regex=r"^ACC-\d{6}$"),
            gxe.ExpectColumnValuesToBeInSet(column="currency", value_set=currencies),
            gxe.ExpectColumnValuesToNotBeNull(column="currency"),
            gxe.ExpectColumnValuesToBeBetween(column="amount_num", min_value=0.01, max_value=10_000_000),
            gxe.ExpectColumnValuesToBeInSet(column="direction", value_set=["DEBIT", "CREDIT"]),
            gxe.ExpectColumnValuesToBeInSet(column="status", value_set=["SUCCESS", "FAILED", "REVERSED"]),
            gxe.ExpectColumnValuesToBeInSet(column="channel", value_set=["UPI", "IMPS", "NEFT", "ATM", "POS", "SWIFT"]),
            gxe.ExpectColumnValuesToBeBetween(column="txn_ts_utc", max_value=LAST_ALLOWED_UTC),
        ],
        "customers": [
            gxe.ExpectColumnValuesToBeUnique(column="customer_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="email"),
            gxe.ExpectColumnValuesToNotBeNull(column="phone"),
            gxe.ExpectColumnValuesToNotBeNull(column="kyc_status"),
            gxe.ExpectColumnValuesToMatchRegex(
                column="email", regex=r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$"),
            gxe.ExpectColumnValuesToMatchRegex(column="phone", regex=r"^[6-9]\d{9}$"),
            gxe.ExpectColumnValuesToBeInSet(column="kyc_status", value_set=["VERIFIED", "PENDING"]),
            gxe.ExpectColumnValuesToMatchRegex(column="date_of_birth", regex=r"^\d{4}-\d{2}-\d{2}$"),
        ],
        "accounts": [
            gxe.ExpectColumnValuesToBeUnique(column="account_id"),
            gxe.ExpectColumnValuesToBeInSet(column="account_type", value_set=["SAVINGS", "CURRENT"]),
            gxe.ExpectColumnValuesToBeInSet(column="branch_code", value_set=branches),
            gxe.ExpectColumnValuesToBeInSet(column="currency", value_set=["INR"]),
        ],
    }


def run(raw_dir: str | Path) -> list[ExpectationResult]:
    frames = _frames(Path(raw_dir))
    context = gx.get_context(mode="ephemeral")
    try:
        context.variables.progress_bars = {"globally": False}
    except Exception:  # older/newer configs: progress bars are cosmetic only
        pass
    source = context.data_sources.add_pandas("arya_bank_extract")
    out: list[ExpectationResult] = []
    for table, expectations in _suites(frames).items():
        batch = source.add_dataframe_asset(table).add_batch_definition_whole_dataframe("whole")
        suite = context.suites.add(gx.ExpectationSuite(name=f"raw_{table}"))
        for e in expectations:
            suite.add_expectation(e)
        validation = context.validation_definitions.add(
            gx.ValidationDefinition(name=f"validate_{table}", data=batch, suite=suite))
        result = validation.run(batch_parameters={"dataframe": frames[table]})
        for r in result.results:
            cfg = r.expectation_config
            out.append(ExpectationResult(
                suite=table,
                expectation=cfg.type,
                column=cfg.kwargs.get("column", ""),
                success=bool(r.success),
                unexpected_count=int(r.result.get("unexpected_count") or 0),
                element_count=int(r.result.get("element_count") or len(frames[table])),
                sample=[str(v) for v in (r.result.get("partial_unexpected_list") or [])[:3]],
            ))
    return out
