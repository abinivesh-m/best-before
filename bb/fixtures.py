"""Recorded evidence.

With no Tavily key the app can still show a full report, but only for a known
sample and only against evidence that was captured from the cited pages. The
report says ``recorded`` and prints the capture date. Anything else is honestly
unverified.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from . import config

_PATH = config.FIXTURES / "samples.json"


@lru_cache(maxsize=1)
def _load(mtime: float) -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def all_data() -> dict:
    try:
        mtime = _PATH.stat().st_mtime
    except FileNotFoundError:
        return {"recorded_on": None, "capture": "", "samples": []}
    return _load(mtime)


def samples() -> list[dict]:
    return all_data().get("samples", [])


def summary_list() -> list[dict]:
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "written": s.get("written"),
            "blurb": s.get("blurb", ""),
            "claims": len(s.get("claims", [])),
        }
        for s in samples()
    ]


def get(sample_id: str | None) -> dict | None:
    if not sample_id:
        return None
    for sample in samples():
        if sample["id"] == sample_id:
            return sample
    return None


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def matches(sample: dict, text: str) -> bool:
    return normalize(sample.get("answer", "")) == normalize(text)


def recorded_on() -> str | None:
    return all_data().get("recorded_on")


def capture_note() -> str:
    return all_data().get("capture", "")


def evidence(claim: dict) -> list[dict]:
    """Normalize recorded sources into the same shape Tavily produces."""
    out = []
    for source in claim.get("sources", []):
        stance = source.get("stance", "unrelated")
        if stance not in ("supports", "contradicts", "unrelated"):
            stance = "unrelated"
        out.append(
            {
                "title": source.get("title", source.get("url", "")),
                "url": source.get("url", ""),
                "content": source.get("content", ""),
                "published_date": source.get("published_date"),
                "score": float(source.get("score", 0.7)),
                "stance": stance,
                "match": float(source.get("match", 1.0 if stance != "unrelated" else 0.0)),
            }
        )
    return out


def claims(sample: dict) -> list[dict]:
    out = []
    for index, claim in enumerate(sample.get("claims", []), start=1):
        out.append(
            {
                "id": claim.get("id") or f"c{index}",
                "text": claim["text"],
                "kind": claim.get("kind", "other"),
                "kind_label": {
                    "pricing": "Pricing",
                    "version": "Version",
                    "regulation": "Regulation",
                    "company": "Company",
                    "other": "Fact",
                }.get(claim.get("kind", "other"), "Fact"),
                "query": claim.get("query") or claim["text"][:120],
                "value": claim.get("value"),
                "recorded_note": claim.get("recorded_note", ""),
                "current_value": claim.get("current_value", ""),
            }
        )
    return out
