"""Each SQL rule must flag exactly the injected defects: no misses, no false alarms."""

import pytest

from arya_dq.rules import RULES
from arya_dq.score import expected_keys


@pytest.mark.parametrize("rule", RULES, ids=lambda r: f"{r.rule_id}-{r.dimension}")
def test_rule_finds_exactly_the_injected_defects(rule, dirty_findings, manifest):
    found = set(dirty_findings[rule.rule_id])
    expected = expected_keys(manifest)[rule.rule_id]
    missed, extra = expected - found, found - expected
    assert not missed, f"{rule.rule_id} missed {len(missed)}: {sorted(missed)[:5]}"
    assert not extra, f"{rule.rule_id} false positives {len(extra)}: {sorted(extra)[:5]}"


@pytest.mark.parametrize("rule", RULES, ids=lambda r: r.rule_id)
def test_rule_is_silent_on_clean_data(rule, clean_findings):
    assert clean_findings[rule.rule_id] == []
