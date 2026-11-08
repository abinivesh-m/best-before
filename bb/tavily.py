"""Live web evidence via Tavily.

If no key is configured, ``search`` returns mode="none" with a reason. It never
invents results: the caller decides whether recorded evidence can stand in, and
the UI repeats that decision back to the user.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from . import config


@dataclass
class EvidenceResult:
    query: str
    results: list[dict] = field(default_factory=list)
    mode: str = "none"          # tavily | recorded | none
    latency_ms: float = 0.0
    error: str = ""


def _clean_date(value) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(text[: len(fmt) + 4].strip(), fmt).date().isoformat()
        except Exception:
            continue
    # last resort: first ISO-looking date in the string
    head = text[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date().isoformat()
    except Exception:
        return None


def normalize(items: list[dict]) -> list[dict]:
    out = []
    for item in items or []:
        url = str(item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        try:
            score = float(item.get("score") or 0.6)
        except (TypeError, ValueError):
            score = 0.6
        out.append(
            {
                "title": str(item.get("title") or url)[:300],
                "url": url,
                "content": str(item.get("content") or item.get("raw_content") or "")[:1200],
                "published_date": _clean_date(item.get("published_date") or item.get("publishedDate")),
                "score": max(0.0, min(1.0, score)),
            }
        )
    return out


async def search(query: str, max_results: int | None = None) -> EvidenceResult:
    started = time.perf_counter()
    if not config.TAVILY_KEY:
        return EvidenceResult(
            query=query,
            mode="none",
            error="TAVILY_API_KEY is not set",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    body = {
        "query": query,
        "max_results": int(max_results or config.TAVILY_RESULTS),
        "search_depth": config.TAVILY_DEPTH,
        "topic": "general",
        "include_answer": False,
        "include_raw_content": False,
        "include_published_date": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.TAVILY_KEY}",
    }
    try:
        async with httpx.AsyncClient(timeout=config.TAVILY_TIMEOUT) as client:
            response = await client.post(config.TAVILY_URL, headers=headers, json=body)
        if response.status_code != 200:
            return EvidenceResult(
                query=query,
                mode="none",
                error=f"HTTP {response.status_code}: {response.text[:200]}",
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        payload = response.json()
        results = normalize(payload.get("results") or [])
        return EvidenceResult(
            query=query,
            results=results,
            mode="tavily" if results else "none",
            error="" if results else "Tavily returned no results",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
    except Exception as exc:
        return EvidenceResult(
            query=query,
            mode="none",
            error=f"{type(exc).__name__}: {exc}",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )


def age_days(published: str | None, now: datetime | None = None) -> float | None:
    if not published:
        return None
    try:
        date = datetime.fromisoformat(published).replace(tzinfo=timezone.utc)
    except Exception:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - date).total_seconds() / 86400)
