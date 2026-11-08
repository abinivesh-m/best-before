from __future__ import annotations

import asyncio

import pytest

from bb import config, fixtures, verify


@pytest.fixture()
def offline(monkeypatch):
    """No Tavily, no gateway, no direct provider - the honest offline path."""
    monkeypatch.setattr(config, "TAVILY_KEY", "")
    monkeypatch.setattr(config, "GROQ_KEY", "")
    monkeypatch.setattr(config, "BIFROST_URL", "")


def test_every_sample_has_claims_and_http_sources():
    samples = fixtures.samples()
    assert len(samples) >= 3
    for sample in samples:
        assert sample["answer"].strip()
        assert sample["claims"]
        for claim in sample["claims"]:
            assert claim["sources"], f"{sample['id']}/{claim['id']} has no evidence"
            for source in claim["sources"]:
                assert source["url"].startswith("https://")
                assert source["stance"] in ("supports", "contradicts", "unrelated")


def test_offline_report_is_labelled_recorded(offline):
    sample = fixtures.get("stack-2023")
    report = asyncio.run(verify.check(sample["answer"], sample["id"]))
    assert report["mode"]["evidence"] == "recorded"
    assert report["mode"]["judge"] == "recorded"
    assert "recorded" in report["mode"]["disclaimer"].lower()
    assert report["verdict"]["status"] == "expired"


@pytest.mark.parametrize("sample_id", ["stack-2023", "pg-upgrade", "ai-act"])
def test_samples_match_their_expected_verdict_offline(offline, sample_id):
    sample = fixtures.get(sample_id)
    report = asyncio.run(verify.check(sample["answer"], sample_id))
    assert report["verdict"]["status"] == sample["expected"]
    assert report["answer"]["sample_matched"] is True
    assert all(claim["status"] != "unverified" for claim in report["claims"])


def test_the_current_sample_only_flags_the_two_wrong_claims(offline):
    sample = fixtures.get("ai-act")
    report = asyncio.run(verify.check(sample["answer"], sample["id"]))
    stale = [claim for claim in report["claims"] if claim["status"] == "stale"]
    current = [claim for claim in report["claims"] if claim["status"] == "current"]
    assert len(stale) == 2
    assert len(current) == 4
    assert report["verdict"]["status"] == "needs_correction"


def test_custom_text_without_evidence_is_unverified(offline):
    report = asyncio.run(verify.check("Python 3.12 is the latest stable release of Python.", None))
    assert report["mode"]["evidence"] == "none"
    assert report["verdict"]["status"] == "unverified"
    assert all(claim["status"] == "unverified" for claim in report["claims"])


def test_mismatched_sample_text_does_not_borrow_evidence(offline):
    sample = fixtures.get("stack-2023")
    report = asyncio.run(verify.check(sample["answer"] + " Extra sentence.", sample["id"]))
    assert report["answer"]["sample_matched"] is False
    assert report["mode"]["evidence"] == "none"
    assert report["verdict"]["status"] == "unverified"
