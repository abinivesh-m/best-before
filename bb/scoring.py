"""Freshness scoring.

One claim:
    freshness = 0.60 * agreement + 0.25 * recency + 0.15 * authority

agreement  how strongly the relevant sources support the claim's specific value
recency    how recently those sources were published, on a half-life per claim type
authority  how close the best relevant source is to the primary source of truth

The weights are deliberately opinionated: whether a source *agrees* matters more
than whether it is new, because a fresh blog repeating an old number is still
wrong. Thresholds live in config (0.72 / 0.45).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from urllib.parse import urlparse

from . import config

KIND_IMPACT = {"pricing": 1.3, "version": 1.2, "regulation": 1.3, "company": 1.0, "other": 0.6}

# How long a verified claim of each type is expected to stay true.
SHELF_LIFE_DAYS = {"pricing": 30, "version": 60, "regulation": 90, "company": 90, "other": 180}

# Recency half-life: pricing moves fast, statutes do not.
HALF_LIFE_DAYS = {"pricing": 120, "version": 240, "regulation": 365, "company": 180, "other": 365}

AUTHORITY = {
    # primary documentation and official registries
    "python.org": 0.96,
    "docs.python.org": 0.95,
    "nodejs.org": 0.96,
    "postgresql.org": 0.96,
    "docs.github.com": 0.95,
    "platform.openai.com": 0.95,
    "eur-lex.europa.eu": 0.98,
    "ec.europa.eu": 0.96,
    "artificialintelligenceact.eu": 0.90,
    "stripe.com": 0.95,
    # vendor source and release tags
    "github.com": 0.90,
    "raw.githubusercontent.com": 0.86,
    "pypi.org": 0.88,
    "npmjs.com": 0.85,
    # curated trackers
    "endoflife.date": 0.82,
    # press
    "reuters.com": 0.78,
    "apnews.com": 0.78,
    "bbc.com": 0.74,
    "theverge.com": 0.68,
    "techcrunch.com": 0.66,
    # community
    "stackoverflow.com": 0.55,
    "reddit.com": 0.28,
    "x.com": 0.22,
    "twitter.com": 0.22,
    "medium.com": 0.38,
}

STATUS_LABEL = {
    "current": "Current",
    "stale": "Stale",
    "uncertain": "Recheck",
    "unverified": "Unverified",
}

VERDICT_LABEL = {
    "current": "CURRENT",
    "needs_correction": "NEEDS CORRECTION",
    "expired": "EXPIRED",
    "recheck": "RECHECK",
    "unverified": "UNVERIFIED",
}

VERDICT_BLURB = {
    "current": "Every checked claim still holds against current sources.",
    "needs_correction": "Some of this answer is no longer true. Read the flagged claims before using it.",
    "expired": "This answer is past its best before. Do not ship it as written.",
    "recheck": "The sources could not settle some claims. Treat those as open questions.",
    "unverified": "No evidence was retrieved, so nothing here is confirmed.",
}


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def authority_of(url: str) -> float:
    host = domain_of(url)
    if not host:
        return 0.4
    best = 0.0
    for known, value in AUTHORITY.items():
        if host == known or host.endswith("." + known):
            best = max(best, value)
    if best:
        return best
    if host.endswith(".gov") or host.endswith(".edu"):
        return 0.85
    if host.endswith(".org"):
        return 0.62
    return 0.52


def recency_of(published: str | None, kind: str, today: date) -> float:
    if not published:
        return 0.55  # unknown date: neither rewarded nor punished
    try:
        published_on = date.fromisoformat(published)
    except Exception:
        return 0.55
    age = max(0, (today - published_on).days)
    half_life = HALF_LIFE_DAYS.get(kind, 365)
    return 0.5 ** (age / half_life)


def agreement_of(sources: list[dict]) -> tuple[float, float]:
    """Weighted support, plus the total weight of evidence that took a side."""
    numerator = 0.0
    denominator = 0.0
    for source in sources:
        stance = source.get("stance", "unrelated")
        if stance not in ("supports", "contradicts"):
            continue
        weight = float(source.get("score", 0.6)) * authority_of(source.get("url", ""))
        match = max(0.0, min(1.0, float(source.get("match", 1.0))))
        denominator += weight
        numerator += weight * (match if stance == "supports" else (1.0 - match))
    if denominator == 0:
        return 0.5, 0.0
    return numerator / denominator, denominator


def score_claim(claim: dict, sources: list[dict], today: date) -> dict:
    kind = claim.get("kind", "other")
    relevant = [s for s in sources if s.get("stance") in ("supports", "contradicts")]

    if not sources:
        freshness, agreement, recency, authority = 0.0, 0.0, 0.0, 0.0
    else:
        agreement, weight = agreement_of(sources)
        pool = relevant or sources
        total = sum(float(s.get("score", 0.6)) for s in pool) or 1.0
        recency = sum(
            float(s.get("score", 0.6)) * recency_of(s.get("published_date"), kind, today) for s in pool
        ) / total
        authority = max((authority_of(s.get("url", "")) for s in pool), default=0.4)
        freshness = 0.60 * agreement + 0.25 * recency + 0.15 * authority

    freshness = max(0.0, min(1.0, freshness))
    if not sources:
        status = "unverified"
    elif freshness >= config.CURRENT_AT:
        status = "current"
    elif freshness < config.STALE_BELOW:
        status = "stale"
    else:
        status = "uncertain"

    return {
        "freshness": round(freshness, 3),
        "score": round(freshness * 100),
        "status": status,
        "status_label": STATUS_LABEL[status],
        "agreement": round(agreement, 3),
        "recency": round(recency, 3),
        "authority": round(authority, 3),
        "evidence_weight": round(weight if sources else 0.0, 2),
    }


def best_before_for(status: str, kind: str, sources: list[dict], today: date) -> tuple[str | None, str | None]:
    shelf = SHELF_LIFE_DAYS.get(kind, 180)
    if status == "unverified":
        return None, None
    if status == "stale":
        dated = [s.get("published_date") for s in sources if s.get("stance") == "contradicts" and s.get("published_date")]
        when = max(dated) if dated else today.isoformat()
        return when, "expired"
    if status == "uncertain":
        return (today + timedelta(days=max(7, shelf // 2))).isoformat(), "provisional"
    return (today + timedelta(days=shelf)).isoformat(), "projected"


def answer_summary(claims: list[dict], today: date) -> dict:
    total = len(claims)
    counts = {key: 0 for key in ("current", "stale", "uncertain", "unverified")}
    for claim in claims:
        counts[claim["status"]] = counts.get(claim["status"], 0) + 1

    verified = [c for c in claims if c["status"] != "unverified"]
    if not verified:
        return {
            "status": "unverified",
            "label": VERDICT_LABEL["unverified"],
            "blurb": VERDICT_BLURB["unverified"],
            "freshness": 0.0,
            "score": 0,
            "best_before": None,
            "best_before_kind": None,
            "headline": "Nothing in this answer could be verified.",
            "counts": {**counts, "total": total},
            "stale_share": 0.0,
        }

    impact_total = sum(KIND_IMPACT.get(c.get("kind", "other"), 1.0) for c in verified)
    freshness = sum(
        KIND_IMPACT.get(c.get("kind", "other"), 1.0) * c["freshness"] for c in verified
    ) / (impact_total or 1.0)
    stale_impact = sum(
        KIND_IMPACT.get(c.get("kind", "other"), 1.0) for c in verified if c["status"] == "stale"
    )
    stale_share = stale_impact / (impact_total or 1.0)

    if counts["stale"] and (stale_share >= 0.35 or freshness < config.STALE_BELOW):
        status = "expired"
    elif counts["stale"]:
        status = "needs_correction"
    elif freshness < config.CURRENT_AT:
        status = "recheck"
    else:
        status = "current"

    if status == "expired":
        headline = (
            f"{counts['stale']} of {len(verified)} claims are past their best before."
            if counts["stale"] != 1
            else f"1 of {len(verified)} claims is past its best before."
        )
    elif status == "needs_correction":
        headline = f"{counts['stale']} of {len(verified)} claims need correcting."
    elif status == "recheck":
        headline = f"{counts['uncertain']} claims could not be settled from current sources."
    else:
        headline = f"All {len(verified)} claims still hold."

    dated = [c["best_before"] for c in verified if c.get("best_before")]
    best_before = min(dated) if dated else None
    kinds = {c.get("best_before_kind") for c in verified if c.get("best_before_kind")}
    kind = "expired" if "expired" in kinds else ("provisional" if "provisional" in kinds else "projected")

    return {
        "status": status,
        "label": VERDICT_LABEL[status],
        "blurb": VERDICT_BLURB[status],
        "freshness": round(freshness, 3),
        "score": round(freshness * 100),
        "best_before": best_before,
        "best_before_kind": kind,
        "headline": headline,
        "counts": {**counts, "total": total},
        "stale_share": round(stale_share, 3),
    }


def label_for_date(value: str | None, kind: str | None) -> str:
    if not value:
        return "No expiry estimate"
    try:
        when = date.fromisoformat(value)
    except Exception:
        return value
    pretty = when.strftime("%d %b %Y").lstrip("0")
    if kind == "expired":
        return f"Expired by {pretty}"
    if kind == "provisional":
        return f"Recheck after {pretty}"
    return f"Best before {pretty}"
