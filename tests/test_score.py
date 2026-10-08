"""Quality score behaves: 100 on clean data, lower on dirty data, and the
17 Sep feed incident shows up as the worst day."""

from arya_dq import score


def test_clean_data_scores_100(clean_db, clean_findings):
    dims = score.dimension_scores(score.rule_scores(clean_db, clean_findings))
    assert score.overall_score(dims) == 100.0
    assert all(v == 100.0 for v in dims.values())


def test_dirty_data_scores_below_100_in_every_dimension(dirty_db, dirty_findings):
    dims = score.dimension_scores(score.rule_scores(dirty_db, dirty_findings))
    assert set(dims) == set(score.DIMENSIONS)
    assert all(v < 100.0 for v in dims.values()), dims


def test_every_day_of_september_is_scored(dirty_db, dirty_findings):
    days = score.daily_scores(dirty_db, dirty_findings)
    assert [d["date"] for d in days][0] == "2026-09-01"
    assert len(days) == 30


def test_feed_incident_is_the_worst_day(dirty_db, dirty_findings):
    days = score.daily_scores(dirty_db, dirty_findings)
    worst = min(days, key=lambda d: d["score"])
    assert worst["date"] == "2026-09-17"


def test_rules_graded_perfectly_against_manifest(dirty_findings, manifest):
    for row in score.grade(dirty_findings, manifest):
        assert row["precision"] == 1.0 and row["recall"] == 1.0, row
