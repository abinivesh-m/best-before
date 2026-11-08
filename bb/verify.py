"""The check pipeline: claims -> evidence -> judgement -> freshness -> report."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import date, datetime, timezone

from . import claims as claims_mod
from . import config, fixtures, llm, scoring, tavily

JUDGE_SYSTEM = """You are a freshness judge. For one claim and a numbered set of web sources, decide what each source says about the claim's specific value, and whether the claim is still true today.

Rules:
- "supports": the source confirms the claim is still true, allowing for paraphrase.
- "contradicts": the source states a different value, says the thing is deprecated or end-of-life, or replaces it with a newer one.
- "unrelated": the source does not address the specific claim.
- Judge the practical assertion, not just its numbers. If the claim says something is current, supported, or the latest, and the source shows that state has ended, that is "contradicts" even when the source repeats the same date or version.
- A future-tense claim ("will be supported until X") is false once X has passed and support has ended.
- A claim that a staged law has a single compliance deadline is "contradicts" when the sources show earlier waves already applying and the stated year covers only a later slice.
- Use only the supplied sources. No outside knowledge. If they do not settle it, use "unrelated".
- "match" is 0..1: how fully the source states the conflicting or supporting value.
- "current_value": if the claim is wrong, state the correct value from the sources in at most 30 words. If it is right, restate it briefly.
- "note": one sentence pointing at the decisive evidence.

Return JSON: {"sources":[{"i":1,"stance":"supports","match":1.0}],"current_value":"...","note":"..."}"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _report_id(text: str) -> str:
    digest = hashlib.sha1(f"{text}{time.time()}".encode("utf-8")).hexdigest()
    return f"bb_{digest[:10]}"


def _apply_judgement(sources: list[dict], data: dict) -> tuple[list[dict], str, str] | None:
    rows = data.get("sources")
    if not isinstance(rows, list):
        return None
    decided: dict[int, tuple[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("i"))
        except (TypeError, ValueError):
            continue
        stance = str(row.get("stance") or "unrelated").lower()
        if stance not in ("supports", "contradicts", "unrelated"):
            stance = "unrelated"
        try:
            match = max(0.0, min(1.0, float(row.get("match", 1.0))))
        except (TypeError, ValueError):
            match = 1.0
        decided[index] = (stance, match)
    if not decided:
        return None
    judged = []
    for index, source in enumerate(sources, start=1):
        stance, match = decided.get(index, ("unrelated", 0.0))
        judged.append({**source, "stance": stance, "match": match})
    return (
        judged,
        str(data.get("current_value") or "").strip()[:280],
        str(data.get("note") or "").strip()[:280],
    )


async def _judge_with_model(claim: dict, sources: list[dict]) -> tuple[list[dict], str, str, llm.LLMResult] | None:
    payload = {
        "today": date.today().isoformat(),
        "claim": {"text": claim["text"], "kind": claim.get("kind"), "asserted_value": claim.get("value")},
        "sources": [
            {
                "i": index,
                "title": source.get("title", ""),
                "url": source.get("url", ""),
                "published_date": source.get("published_date"),
                "content": source.get("content", "")[:900],
            }
            for index, source in enumerate(sources[:6], start=1)
        ],
    }
    result = await llm.complete_json(
        JUDGE_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        max_tokens=900,
    )
    if not result.ok:
        return None
    applied = _apply_judgement(sources[:6], result.data)
    if applied is None:
        return None
    judged, current_value, note = applied
    return judged, current_value, note, result


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for piece in value.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def heuristic_judge(claim: dict, sources: list[dict]) -> list[dict]:
    """A transparent fallback for when no model is configured.

    It only knows two tricks: a newer version number contradicts an older one, and
    high word overlap supports. The report labels this method, so nobody mistakes
    it for a model verdict.
    """
    claim_text = claim["text"].lower()
    claim_versions = [_version_tuple(v) for v in re.findall(r"\d+(?:\.\d+)+", claim["text"])]
    claim_versions = [v for v in claim_versions if v]
    claim_words = {w for w in re.findall(r"[a-z0-9]{4,}", claim_text)}
    out = []
    for source in sources:
        content = source.get("content", "").lower()
        source_versions = [_version_tuple(v) for v in re.findall(r"\d+(?:\.\d+)+", content)]
        source_versions = [v for v in source_versions if v]
        stance, match = "unrelated", 0.0
        if claim_versions and source_versions:
            if max(source_versions) > max(claim_versions):
                stance, match = "contradicts", 0.85
            elif max(source_versions) == max(claim_versions):
                stance, match = "supports", 0.7
        if stance == "unrelated":
            source_words = {w for w in __import__("re").findall(r"[a-z0-9]{4,}", content)}
            overlap = len(claim_words & source_words) / max(1, len(claim_words))
            if overlap >= 0.45:
                stance, match = "supports", min(1.0, overlap)
        out.append({**source, "stance": stance, "match": match})
    return out


def _decorate(source: dict) -> dict:
    url = source.get("url", "")
    return {
        "title": source.get("title") or scoring.domain_of(url) or url,
        "url": url,
        "domain": scoring.domain_of(url),
        "published_date": source.get("published_date"),
        "content": source.get("content", "")[:420],
        "score": round(float(source.get("score", 0.6)), 3),
        "authority": round(scoring.authority_of(url), 2),
        "stance": source.get("stance", "unrelated"),
        "match": round(float(source.get("match", 0.0)), 2),
    }


async def _gather_evidence(claim: dict, sample_claim: dict | None, live: bool) -> tuple[list[dict], str, str]:
    error = ""
    if live:
        result = await tavily.search(claim["query"])
        if result.mode == "tavily":
            return result.results, "tavily", ""
        error = result.error
    if sample_claim is not None:
        recorded = fixtures.evidence(sample_claim)
        if recorded:
            return recorded, "recorded", error
    return [], "none", error or "no evidence source available"


async def check(text: str, sample_id: str | None = None) -> dict:
    started = time.perf_counter()
    text = (text or "").strip()[: config.MAX_TEXT]
    today = date.today()
    trace: list[dict] = []

    sample = fixtures.get(sample_id)
    sample_matched = bool(sample and fixtures.matches(sample, text))
    if sample and not sample_matched:
        sample = None

    live = config.tavily_ready()
    trace.append(
        {
            "step": "read",
            "label": f"read {len(text.split())} words" + (f" · sample {sample_id}" if sample else ""),
            "ms": 0.0,
        }
    )

    extract_started = time.perf_counter()
    extraction = "recorded" if sample else "heuristic"
    extraction_result: llm.LLMResult | None = None
    if sample:
        claim_list = fixtures.claims(sample)
    else:
        claim_list, extraction, extraction_result = await claims_mod.extract(text, use_model=True)
    trace.append(
        {
            "step": "claims",
            "label": f"{len(claim_list)} claims · {extraction} extraction",
            "ms": round((time.perf_counter() - extract_started) * 1000, 1),
        }
    )

    sample_claims = (
        {c.get("id", f"c{i + 1}"): c for i, c in enumerate(sample.get("claims", []))} if sample else {}
    )

    evidence_started = time.perf_counter()
    semaphore = asyncio.Semaphore(max(1, config.LLM_WORKERS))
    evidence_rows: list[tuple[list[dict], str, str]] = [([], "none", "") for _ in claim_list]

    async def fetch(index: int, claim: dict) -> None:
        async with semaphore:
            evidence_rows[index] = await _gather_evidence(claim, sample_claims.get(claim["id"]), live)

    await asyncio.gather(*(fetch(i, c) for i, c in enumerate(claim_list)))
    evidence_ms = round((time.perf_counter() - evidence_started) * 1000, 1)

    evidence_modes = [row[1] for row in evidence_rows]
    if "tavily" in evidence_modes:
        evidence_mode = "tavily"
    elif "recorded" in evidence_modes:
        evidence_mode = "recorded"
    else:
        evidence_mode = "none"
    evidence_errors = sorted({row[2] for row in evidence_rows if row[2]})
    trace.append(
        {
            "step": "evidence",
            "label": (
                f"{len(claim_list)} live Tavily searches"
                if evidence_mode == "tavily"
                else f"{sum(1 for m in evidence_modes if m == 'recorded')} recorded evidence sets"
                if evidence_mode == "recorded"
                else "no evidence retrieved"
            ),
            "ms": evidence_ms,
        }
    )

    judge_started = time.perf_counter()
    judged_rows: list[tuple[list[dict], str, str, str]] = [([], "none", "", "") for _ in claim_list]
    routing_hits: list[llm.LLMResult] = []

    async def judge(index: int, claim: dict) -> None:
        sources, mode, _ = evidence_rows[index]
        sample_claim = sample_claims.get(claim["id"])
        if not sources:
            judged_rows[index] = ([], "none", "", "")
            return

        async with semaphore:
            outcome = await _judge_with_model(claim, sources) if config.llm_ready() else None

        if outcome is not None:
            judged, current_value, note, result = outcome
            routing_hits.append(result)
            judged_rows[index] = (judged, result.mode, current_value, note)
            return

        if sample_claim is not None:
            judged = fixtures.evidence(sample_claim)
            judged_rows[index] = (
                judged,
                "recorded",
                sample_claim.get("current_value", ""),
                sample_claim.get("recorded_note", ""),
            )
            return

        judged = heuristic_judge(claim, sources)
        judged_rows[index] = (judged, "heuristic", "", "")

    await asyncio.gather(*(judge(i, c) for i, c in enumerate(claim_list)))
    judge_ms = round((time.perf_counter() - judge_started) * 1000, 1)

    judge_modes = [row[1] for row in judged_rows]
    if "bifrost" in judge_modes:
        judge_mode = "bifrost"
    elif "direct" in judge_modes:
        judge_mode = "direct"
    elif "recorded" in judge_modes:
        judge_mode = "recorded"
    elif "heuristic" in judge_modes:
        judge_mode = "heuristic"
    else:
        judge_mode = "none"

    if judge_mode == "bifrost":
        judge_label = "Bifrost gateway verdicts"
    elif judge_mode == "direct":
        judge_label = f"direct {config.GROQ_MODEL} verdicts"
    elif judge_mode == "recorded":
        judge_label = "recorded verdicts (no model)"
    elif judge_mode == "heuristic":
        judge_label = "heuristic verdicts (no model)"
    else:
        judge_label = "not judged"
    trace.append(
        {
            "step": "judge",
            "label": f"{len(claim_list)} {judge_label}",
            "ms": judge_ms,
        }
    )

    score_started = time.perf_counter()
    built: list[dict] = []
    for index, claim in enumerate(claim_list):
        sources, _evidence_mode, _ = evidence_rows[index]
        judged, claim_judge_mode, current_value, note = judged_rows[index]
        scored = scoring.score_claim(claim, judged, today)
        best_before, best_kind = scoring.best_before_for(scored["status"], claim.get("kind", "other"), judged, today)

        ordered = sorted(
            judged,
            key=lambda s: (
                0 if s.get("stance") == "contradicts" else 1 if s.get("stance") == "supports" else 2,
                -float(s.get("score", 0)),
            ),
        )
        built.append(
            {
                **claim,
                **scored,
                "best_before": best_before,
                "best_before_kind": best_kind,
                "best_before_label": scoring.label_for_date(best_before, best_kind),
                "judge": claim_judge_mode,
                "current_value": current_value or (sample_claims.get(claim["id"], {}) or {}).get("current_value", ""),
                "note": note or (sample_claims.get(claim["id"], {}) or {}).get("recorded_note", ""),
                "sources": [_decorate(s) for s in ordered],
                "source_count": len(ordered),
            }
        )

    summary = scoring.answer_summary(built, today)
    trace.append(
        {
            "step": "score",
            "label": f"freshness {summary['score']}/100 · {summary['label'].lower()}",
            "ms": round((time.perf_counter() - score_started) * 1000, 1),
        }
    )

    gateway_hits = [r for r in routing_hits if r.mode == "bifrost"]
    gateway_used = bool(gateway_hits)
    llm_errors = sorted({r.error for r in routing_hits if r.error})
    total_ms = round((time.perf_counter() - started) * 1000, 1)

    if evidence_mode == "recorded":
        disclaimer = (
            f"Evidence recorded {fixtures.recorded_on()} from the cited pages. "
            "Add TAVILY_API_KEY to verify against live search instead."
        )
    elif evidence_mode == "none":
        disclaimer = "No evidence was retrieved, so no claim is confirmed. Unverified is not the same as current."
    else:
        disclaimer = ""

    return {
        "id": _report_id(text),
        "checked_at": _now().isoformat(timespec="seconds"),
        "answer": {
            "text": text,
            "chars": len(text),
            "words": len(text.split()),
            "sample_id": sample["id"] if sample else None,
            "sample_title": sample["title"] if sample else None,
            "sample_written": sample.get("written") if sample else None,
            "sample_matched": sample_matched,
        },
        "verdict": summary,
        "claims": built,
        "mode": {
            "evidence": evidence_mode,
            "judge": judge_mode,
            "extraction": extraction,
            "live": evidence_mode == "tavily",
            "model": (gateway_hits or routing_hits)[-1].model if (gateway_hits or routing_hits) else (config.GROQ_MODEL if config.GROQ_KEY else ""),
            "gateway": {
                "used": gateway_used,
                "provider": gateway_hits[-1].provider if gateway_hits else "",
                "model": gateway_hits[-1].model if gateway_hits else "",
                "is_fallback": any(r.is_fallback for r in gateway_hits),
                "fallbacks": config.BIFROST_FALLBACKS,
                "url": config.BIFROST_URL,
            },
            "llm_error": "; ".join(llm_errors)[:400],
            "evidence_error": "; ".join(evidence_errors)[:400],
            "recorded_on": fixtures.recorded_on(),
            "disclaimer": disclaimer,
        },
        "trace": trace,
        "timings": {
            "total_ms": total_ms,
            "evidence_ms": evidence_ms,
            "judge_ms": judge_ms,
        },
    }
