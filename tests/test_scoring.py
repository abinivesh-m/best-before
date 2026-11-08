from __future__ import annotations

from datetime import date

from bb import config, scoring


def test_authority_tiers_are_ordered():
    assert scoring.authority_of("https://www.python.org/downloads/") > scoring.authority_of(
        "https://blog.example.com/post"
    )
    assert scoring.authority_of("https://blog.example.com/post") > scoring.authority_of(
        "https://reddit.com/r/python"
    )
    assert scoring.authority_of("https://ec.europa.eu/ai") >= 0.95


def test_recency_decays_and_handles_missing_dates():
    today = date(2026, 11, 8)
    fresh = scoring.recency_of("2026-11-01", "version", today)
    old = scoring.recency_of("2024-01-01", "version", today)
    assert 0.0 <= old < fresh <= 1.0
    assert scoring.recency_of(None, "version", today) == 0.55


def test_agreement_counts_contradictions_not_volume():
    url = "https://www.python.org/downloads/"
    sources = [
        {"url": url, "score": 0.9, "stance": "contradicts", "match": 1.0},
        {"url": "https://blog.example.com", "score": 0.9, "stance": "supports", "match": 1.0},
    ]
    agreement, weight = scoring.agreement_of(sources)
    assert weight > 0
    assert 0.0 < agreement < 0.5  # the primary source outweighs the blog


def test_a_claim_with_no_evidence_is_unverified():
    claim = {"id": "c1", "text": "Python 3.12 is the latest release.", "kind": "version"}
    scored = scoring.score_claim(claim, [], date(2026, 11, 8))
    assert scored["status"] == "unverified"
    assert scored["score"] == 0


def test_official_contradiction_makes_a_claim_stale():
    claim = {"id": "c1", "text": "Python 3.12 is the latest release.", "kind": "version"}
    sources = [
        {
            "url": "https://www.python.org/downloads/",
            "score": 0.95,
            "stance": "contradicts",
            "match": 1.0,
            "published_date": "2026-10-25",
        }
    ]
    scored = scoring.score_claim(claim, sources, date(2026, 11, 8))
    assert scored["status"] == "stale"
    assert scored["agreement"] == 0.0
    assert scored["score"] < int(config.STALE_BELOW * 100)


def test_supported_recent_claim_is_current():
    claim = {"id": "c1", "text": "python -m venv creates a virtual environment.", "kind": "other"}
    sources = [
        {
            "url": "https://docs.python.org/3/library/venv.html",
            "score": 0.9,
            "stance": "supports",
            "match": 1.0,
            "published_date": "2026-10-01",
        }
    ]
    scored = scoring.score_claim(claim, sources, date(2026, 11, 8))
    assert scored["status"] == "current"
    assert scored["score"] >= int(config.CURRENT_AT * 100)


def test_best_before_is_projected_for_current_and_dated_for_stale():
    today = date(2026, 11, 8)
    projected, kind = scoring.best_before_for("current", "pricing", [], today)
    assert projected == "2026-12-08"  # 30-day shelf life
    assert kind == "projected"

    expired, kind = scoring.best_before_for(
        "stale",
        "version",
        [{"stance": "contradicts", "published_date": "2026-07-20"}],
        today,
    )
    assert expired == "2026-07-20"
    assert kind == "expired"


def test_answer_summary_expires_on_high_impact_stale_claims():
    claims = [
        {"kind": "version", "freshness": 0.2, "status": "stale", "best_before": "2026-07-20", "best_before_kind": "expired"},
        {"kind": "other", "freshness": 0.9, "status": "current", "best_before": "2027-05-01", "best_before_kind": "projected"},
    ]
    summary = scoring.answer_summary(claims, date(2026, 11, 8))
    assert summary["status"] == "expired"
    assert summary["best_before"] == "2026-07-20"


def test_answer_summary_does_not_cry_wolf():
    claims = [
        {"kind": "regulation", "freshness": 0.87, "status": "current", "best_before": "2027-02-06", "best_before_kind": "projected"}
        for _ in range(4)
    ]
    summary = scoring.answer_summary(claims, date(2026, 11, 8))
    assert summary["status"] == "current"
    assert summary["label"] == "CURRENT"
