"""Turn an answer into a small set of checkable claims.

The model does this when it is available. The regex path is a real fallback, not
a mock: it is used offline, and the report labels it as heuristic extraction.
"""
from __future__ import annotations

import json
import re

from . import config, llm

KINDS = ("pricing", "version", "regulation", "company", "other")
KIND_LABEL = {
    "pricing": "Pricing",
    "version": "Version",
    "regulation": "Regulation",
    "company": "Company",
    "other": "Fact",
}

PRICING = re.compile(
    r"(\$|€|£|₹|\bUSD\b|\bEUR\b|\bINR\b|\bprice[sd]?\b|\bfee[s]?\b|\bcost[s]?\b|"
    r"\bper (month|year|user|seat|request|token|call)\b|\bmonthly\b|\bsubscription\b|\bbilling\b|%)",
    re.I,
)
VERSION = re.compile(
    r"(\bv?\d+\.\d+(\.\d+)?\b|\bversion\b|\brelease[sd]?\b|\blatest\b|\bLTS\b|\bdeprecat|"
    r"\bend[- ]of[- ]life\b|\bEOL\b|\bSDK\b|\bAPI v\d|\brunner\b|\bnode\.?js\b|\bpython\b)",
    re.I,
)
REGULATION = re.compile(
    r"(\bact\b|\bregulat|\blaw\b|\bcompliance\b|\bGDPR\b|\bAI Act\b|\bdirective\b|"
    r"\bappl(?:y|ies|ied)\b|\bdeadline\b|\beffective\b|\bEU\b|\bSEC\b|\bDPDP\b)",
    re.I,
)
COMPANY = re.compile(
    r"(\bfounded\b|\bheadquarter|\bCEO\b|\bemploye|\bacquired\b|\bvaluation\b|\bIPO\b|"
    r"\bcompany\b|\bstartup\b|\bInc\.?\b|\bLtd\.?\b)",
    re.I,
)
CHECKABLE = re.compile(r"(\d|\b(is|are|was|were|has|have|will|now|currently|latest|since)\b)", re.I)
OPINION = re.compile(
    r"^(i think|i believe|in my opinion|you should|we recommend|try to|consider|remember to|"
    r"note that|for example|here is|here's|let's|this guide|this post)\b",
    re.I,
)
STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "of", "to", "in", "on",
    "for", "with", "and", "or", "that", "this", "it", "its", "as", "at", "by", "from", "so",
    "we", "you", "your", "our", "their", "they", "i", "can", "will", "would", "should", "may",
    "currently", "latest", "newest", "release", "releases", "version", "versions", "use", "using",
    "all", "any", "some", "more", "most", "very", "just", "still", "now", "then", "than",
}


def classify(text: str) -> str:
    scores = {
        "pricing": len(PRICING.findall(text)),
        "version": len(VERSION.findall(text)),
        "regulation": len(REGULATION.findall(text)),
        "company": len(COMPANY.findall(text)),
    }
    kind, score = max(scores.items(), key=lambda pair: pair[1])
    return kind if score > 0 else "other"


def _query(text: str, kind: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+.\-/']*", text)
    keep = [w for w in words if w.lower() not in STOP]
    base = " ".join(keep[:14]).strip()
    suffix = {
        "pricing": "current pricing official",
        "version": "latest current version official",
        "regulation": "current requirements official",
        "company": "latest company information",
        "other": "current official",
    }[kind]
    return f"{base} {suffix}".strip()[:220]


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text)
    chunks = []
    for block in re.split(r"\n\s*\n|\n\s*[-*•]\s+", text):
        block = re.sub(r"\s+", " ", block).strip()
        if not block:
            continue
        chunks.extend(re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", block))
    return [c.strip(" -•\t") for c in chunks if c.strip()]


def heuristic_extract(text: str, limit: int | None = None) -> list[dict]:
    limit = limit or config.MAX_CLAIMS
    claims: list[dict] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        if not (25 <= len(sentence) <= 420):
            continue
        if OPINION.match(sentence):
            continue
        if not CHECKABLE.search(sentence):
            continue
        # Require something nameable or numeric: an instruction with no fact is not checkable.
        if not (re.search(r"[A-Z][A-Za-z0-9+.\-]{2,}", sentence) or re.search(r"\d", sentence)):
            continue
        key = re.sub(r"\W+", " ", sentence.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        kind = classify(sentence)
        claims.append(
            {
                "id": f"c{len(claims) + 1}",
                "text": sentence,
                "kind": kind,
                "kind_label": KIND_LABEL[kind],
                "query": _query(sentence, kind),
                "value": None,
            }
        )
        if len(claims) >= limit:
            break
    return claims


EXTRACT_SYSTEM = """You extract verifiable factual claims from an AI-generated answer.

Rules:
- Return at most 8 claims. Prefer claims about pricing, API/library versions, regulations, or company facts.
- A claim must be a single, self-contained factual statement. Copy the answer's wording, trimmed if needed.
- Skip opinions, advice, instructions, and anything that cannot change over time.
- "kind" must be one of: pricing, version, regulation, company, other.
- "query" must be a short web search that would surface the authoritative current source for that claim.
- "value" is the specific figure/version/date asserted, or null.

Return JSON: {"claims":[{"text":"...","kind":"version","query":"...","value":"3.12"}]}"""


async def extract(text: str, *, use_model: bool = True) -> tuple[list[dict], str, "llm.LLMResult | None"]:
    """Return (claims, method, model_result). method is model|heuristic."""
    if use_model and config.llm_ready():
        result = await llm.complete_json(
            EXTRACT_SYSTEM,
            json.dumps({"answer": text[: config.MAX_TEXT]}),
            max_tokens=1100,
        )
        if result.ok and isinstance(result.data.get("claims"), list):
            claims = []
            for index, raw in enumerate(result.data["claims"][: config.MAX_CLAIMS], start=1):
                if not isinstance(raw, dict):
                    continue
                claim_text = str(raw.get("text") or "").strip()
                if len(claim_text) < 12:
                    continue
                kind = str(raw.get("kind") or "").lower()
                if kind not in KINDS:
                    kind = classify(claim_text)
                claims.append(
                    {
                        "id": f"c{index}",
                        "text": claim_text[:500],
                        "kind": kind,
                        "kind_label": KIND_LABEL[kind],
                        "query": (str(raw.get("query") or "").strip() or _query(claim_text, kind))[:220],
                        "value": (str(raw["value"])[:120] if raw.get("value") else None),
                    }
                )
            if claims:
                return claims, "model", result
        # Model answered but produced nothing usable: fall through, keeping the error.
        claims = heuristic_extract(text)
        return claims, "heuristic", result

    return heuristic_extract(text), "heuristic", None
