"""Great Expectations suites: all green on clean data, and on dirty data they
fail exactly where defects were injected, with matching counts."""

import pytest

from arya_dq import expectations


@pytest.fixture(scope="module")
def dirty_results(dirty_raw):
    return {(r.suite, r.expectation, r.column): r for r in expectations.run(dirty_raw)}


@pytest.fixture(scope="module")
def clean_results(clean_raw):
    return expectations.run(clean_raw)


def test_clean_extract_passes_every_expectation(clean_results):
    failed = [(r.suite, r.expectation, r.column) for r in clean_results if not r.success]
    assert failed == []


def _keys(manifest, defect_id):
    return manifest["defects"][defect_id]["keys"]


def test_failing_expectations_are_exactly_the_injected_ones(dirty_results):
    failed = {k for k, r in dirty_results.items() if not r.success}
    assert failed == {
        ("transactions", "expect_column_values_to_be_unique", "txn_id"),
        ("transactions", "expect_column_values_to_be_in_set", "currency"),
        ("transactions", "expect_column_values_to_not_be_null", "currency"),
        ("transactions", "expect_column_values_to_be_between", "amount_num"),
        ("transactions", "expect_column_values_to_be_between", "txn_ts_utc"),
        ("customers", "expect_column_values_to_not_be_null", "email"),
        ("customers", "expect_column_values_to_not_be_null", "phone"),
        ("customers", "expect_column_values_to_not_be_null", "kyc_status"),
        ("customers", "expect_column_values_to_match_regex", "email"),
        ("customers", "expect_column_values_to_match_regex", "phone"),
        ("accounts", "expect_column_values_to_be_in_set", "branch_code"),
    }


def test_counts_match_the_manifest(dirty_results, manifest):
    r = dirty_results
    # GX counts every copy of a duplicated value, so each duplicated id shows twice
    assert r[("transactions", "expect_column_values_to_be_unique", "txn_id")].unexpected_count == 2 * len(_keys(manifest, "DQ-01"))
    bad_currency = (r[("transactions", "expect_column_values_to_be_in_set", "currency")].unexpected_count
                    + r[("transactions", "expect_column_values_to_not_be_null", "currency")].unexpected_count)
    assert bad_currency == len(_keys(manifest, "DQ-03"))
    assert r[("transactions", "expect_column_values_to_be_between", "amount_num")].unexpected_count == len(_keys(manifest, "DQ-04"))
    assert r[("transactions", "expect_column_values_to_be_between", "txn_ts_utc")].unexpected_count == len(_keys(manifest, "DQ-06"))
    missing = sum(r[("customers", "expect_column_values_to_not_be_null", c)].unexpected_count
                  for c in ("email", "phone", "kyc_status"))
    assert missing == len(_keys(manifest, "DQ-02"))
    assert r[("customers", "expect_column_values_to_match_regex", "email")].unexpected_count == len(_keys(manifest, "DQ-07"))
    assert r[("customers", "expect_column_values_to_match_regex", "phone")].unexpected_count == len(_keys(manifest, "DQ-08"))
    assert r[("accounts", "expect_column_values_to_be_in_set", "branch_code")].unexpected_count == len(_keys(manifest, "DQ-12"))
