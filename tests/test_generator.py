"""The synthetic extract is deterministic and the manifest is complete."""

from arya_dq import generate


def test_same_seed_same_data():
    a, b = generate.build(seed=7, customers=200, transactions=2000), generate.build(seed=7, customers=200, transactions=2000)
    assert a.transactions == b.transactions
    assert a.customers == b.customers
    assert a.manifest == b.manifest


def test_different_seed_different_data():
    a, b = generate.build(seed=7, customers=200, transactions=2000), generate.build(seed=8, customers=200, transactions=2000)
    assert a.transactions != b.transactions


def test_volumes(manifest):
    counts = manifest["counts"]
    assert counts["customers"] == 1000
    assert counts["branches"] == 20
    assert 1200 <= counts["accounts"] <= 1500
    assert counts["transactions"] > 50_000  # 50,000 clean rows + injected bad rows


def test_every_defect_type_is_injected(manifest):
    for defect_id, defect in manifest["defects"].items():
        assert defect["keys"], f"{defect_id} has no injected records"


def test_clean_extract_has_no_defects(clean_raw):
    import json

    m = json.loads((clean_raw / "manifest.json").read_text())
    assert all(not d["keys"] for d in m["defects"].values())
    assert m["counts"]["transactions"] == 50_000


def test_no_real_looking_identifiers(manifest, dirty_raw):
    """Synthetic only: example.com emails, ACC- account ids, fictional bank codes."""
    import csv

    with open(dirty_raw / "accounts.csv") as fh:
        assert all(r["account_id"].startswith("ACC-") for r in csv.DictReader(fh))
    broken = set(manifest["defects"]["DQ-02"]["keys"]) | set(manifest["defects"]["DQ-07"]["keys"])
    with open(dirty_raw / "customers.csv") as fh:
        emails = [r["email"] for r in csv.DictReader(fh) if r["customer_id"] not in broken]
    assert emails and all(e.endswith("@example.com") for e in emails)
