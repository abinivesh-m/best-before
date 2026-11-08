# Architecture

Best Before is a single Python service with a static front end. There is no build step, no database, and no background worker: a check is one request that fans out to search and model calls, scores the result, and returns a complete, self-describing report.

```
POST /api/check
  { text, sample_id? }
        │
        ▼
   verify.check
        │
        ├── 1. resolve sample (exact text match only, otherwise ignored)
        ├── 2. extract claims        model → heuristic
        ├── 3. gather evidence       Tavily → recorded → none
        ├── 4. judge stances         Bifrost → direct → recorded → heuristic
        ├── 5. score each claim      agreement / recency / authority
        └── 6. summarise + date     weighted verdict, best-before, trace, modes
```

Each stage is allowed to fail independently, and every failure changes a label that the report carries. The invariant is: **the report may be weaker, but it never claims a stronger mode than what actually ran.**

---

## 1. Claims (`bb/claims.py`)

The model is asked for up to eight self-contained factual statements, each with:

- `text` — the assertion, trimmed but not paraphrased
- `kind` — `pricing | version | regulation | company | other`
- `query` — a web search that would surface the authoritative current source
- `value` — the specific figure, version, or date being asserted

Rules in the system prompt exclude opinions, advice, and instructions, and require one fact per claim. The model returns JSON, which `bb/llm.py` parses even when it is wrapped in prose or a fence.

If the model is unavailable or returns nothing usable, `heuristic_extract` splits the text into sentences and keeps ones that are 25–420 characters, contain a digit or a present-tense verb, and name something. Classification is regex-based. This path is not a mock: it really does extract claims, it is just less selective, and the report labels it `heuristic`.

When the text exactly matches a stored sample, the claims are taken from `fixtures/samples.json`. This is what makes an offline demo possible without fabricating evidence for arbitrary text.

---

## 2. Evidence (`bb/tavily.py`, `bb/fixtures.py`)

For each claim, one Tavily `/search` request:

```json
{
  "query": "<claim query>",
  "max_results": 5,
  "search_depth": "advanced",
  "topic": "general",
  "include_answer": false,
  "include_raw_content": false,
  "include_published_date": true
}
```

`normalize()` keeps `title`, `url`, `content`, `published_date`, and `score`, discards non-HTTP URLs, and clamps Tavily's relevance score to `[0, 1]`. `published_date` is parsed from several formats into `YYYY-MM-DD` because the scorer needs a stable date.

Evidence modes:

- `tavily` — at least one claim got live results
- `recorded` — no key or no results, and the sample has captured evidence
- `none` — nothing usable; the claim will be `unverified`

Recorded evidence is stored in the same shape Tavily returns, plus `stance` and `match`. It is only used when the pasted text exactly matches the sample's stored answer, so a user cannot edit a sample and inherit its verdicts. The verdict banner prints the capture date.

Each claim is fetched concurrently through a semaphore (`LLM_WORKERS`, default 4).

---

## 3. Judgement (`bb/verify.py`, `bb/llm.py`)

The judge sees one claim and up to six numbered sources, each with title, URL, publication date, and a 900-character excerpt. It returns, for every source, `stance` (`supports | contradicts | unrelated`) and `match` (0–1), plus `current_value` and a one-sentence `note`.

Two prompt rules matter more than the rest:

- Judge the *practical assertion*, not the number. "Node 18 is the Active LTS" is contradicted by a schedule showing Node 26 is Active LTS and 18 is EOL, even though no sentence says "18 is wrong".
- A future-tense claim is false once its window has passed. "12 is supported until November 2024" is false in 2026.

The model is instructed to use only the supplied sources and to answer `unrelated` when they do not settle the claim. That produces `uncertain` rather than a false accusation.

### Gateway use

Every model call goes through `llm.complete_json`:

1. If `BIFROST_URL` is set and the gateway is not in cooldown, POST to `{BIFROST_URL}/v1/chat/completions` with an OpenAI-shaped body plus `fallbacks`.
2. Read `extra_fields.routing_info` from the response and return `mode="bifrost"` with the provider, model, fallback flag, and gateway latency.
3. If the gateway is unreachable, cool down for 30 s (so a dead gateway does not cost one timeout per claim), then try the provider directly and return `mode="direct"`.
4. If neither works, return `mode="offline"` with the error; the caller falls back to recorded verdicts or the heuristic.

The UI only displays "Bifrost gateway" when at least one response actually came back from the gateway. The verdict panel prints the routed model and marks a fallback route when Bifrost reports one. This is the difference between integrating a gateway and mentioning one.

---

## 4. Scoring (`bb/scoring.py`)

Per claim:

```
freshness = 0.60 * agreement + 0.25 * recency + 0.15 * authority
```

**Agreement.** Only sources that took a side count. Each is weighted by `score × authority(url)`; a supporting source contributes `match`, a contradicting source contributes `1 - match`. The result is weighted support in `[0, 1]`, plus the total weight of deciding evidence. A single 0.96-authority contradiction outweighs several 0.5-authority agreements, which is the intended behaviour: the primary source usually settles the question.

**Recency.** `0.5 ** (age_days / half_life)`, weighted by source relevance. Half-lives: pricing 120 days, version 240, regulation 365, company 180, other 365. Undated sources are neutral (0.55), not punished.

**Authority.** Longest-suffix match against a visible domain table in `bb/scoring.py`; TLD fallbacks for `.gov`, `.edu`, `.org`; 0.52 otherwise. The table is small and inspectable on purpose.

Status thresholds: `>= 0.72` current, `< 0.45` stale, otherwise uncertain. No sources at all means `unverified` with a score of 0.

**Answer-level verdict.** Claim freshness is averaged with kind weights (pricing 1.3, version 1.2, regulation 1.3, company 1.0, other 0.6). The answer is:

- `expired` if any claim is stale and stale impact is at least 35% or overall freshness is below 0.45
- `needs_correction` if there is any stale claim
- `recheck` if nothing is stale but freshness is below 0.72
- `current` otherwise

The high-impact rule is deliberate: a stale `pip install` line makes the answer unusable even if the prose around it is fine.

**Best before.** A current claim gets today + the shelf life for its kind (pricing 30, version 60, regulation 90, company 90, other 180). A stale claim gets the newest contradiction date and is labelled `expired`. The answer's date is the earliest claim date, so the whole answer expires when its first claim does.

---

## 5. Report contract

```jsonc
{
  "id": "bb_…",
  "checked_at": "2026-11-08T15:20:02+00:00",
  "answer": { "text": "…", "words": 214, "sample_id": "stack-2023", "sample_matched": true },
  "verdict": {
    "status": "expired",
    "label": "EXPIRED",
    "freshness": 0.38,
    "score": 38,
    "best_before": "2026-07-20",
    "best_before_kind": "expired",
    "counts": { "current": 2, "stale": 6, "uncertain": 0, "unverified": 0, "total": 8 },
    "stale_share": 0.62
  },
  "claims": [
    {
      "text": "Python 3.12 is the latest stable release.",
      "kind": "version",
      "query": "latest stable Python release version python.org",
      "value": "3.12",
      "freshness": 0.38,
      "score": 38,
      "status": "stale",
      "agreement": 0.0, "recency": 0.96, "authority": 0.96,
      "current_value": "Python 3.15.2 is the current release (25 Oct 2026).",
      "note": "…",
      "best_before": "2026-07-20",
      "sources": [
        { "title": "Download Python", "url": "…", "domain": "python.org",
          "stance": "contradicts", "match": 1.0, "authority": 0.96,
          "published_date": "2026-10-25", "content": "…" }
      ]
    }
  ],
  "mode": {
    "evidence": "recorded",
    "judge": "bifrost",
    "extraction": "recorded",
    "gateway": { "used": true, "provider": "groq", "model": "openai/gpt-oss-120b",
                 "is_fallback": false, "fallbacks": ["groq/qwen/qwen3-32b"] },
    "llm_error": "",
    "evidence_error": "",
    "recorded_on": "2026-11-08",
    "disclaimer": "Evidence recorded 2026-11-08 from the cited pages. …"
  },
  "trace": [
    { "step": "read", "label": "read 214 words · sample stack-2023", "ms": 0.0 },
    { "step": "claims", "label": "8 claims · recorded extraction", "ms": 0.1 },
    { "step": "evidence", "label": "8 recorded evidence sets", "ms": 0.3 },
    { "step": "judge", "label": "8 Bifrost gateway verdicts", "ms": 4120.2 },
    { "step": "score", "label": "freshness 38/100 · expired", "ms": 0.4 }
  ],
  "timings": { "total_ms": 4211.0, "evidence_ms": 0.3, "judge_ms": 4120.2 }
}
```

The `mode` block is the honesty contract. The `trace` block is the audit trail: every stage, what it did, and how long it took. Both are rendered in the UI and included in the copy/download actions.

---

## 6. API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | the interface |
| `GET` | `/api/health` | which modes are available, gateway reachability, thresholds |
| `GET` | `/api/samples` | demo answers plus capture metadata |
| `GET` | `/api/gateway` | gateway probe, configured model, fallback chain |
| `POST` | `/api/check` | `{ text, sample_id? }` → report |

`/api/check` rejects text shorter than 12 characters (422) and caps it at 8,000 (`MAX_TEXT`). A failed check returns 500 with the exception type; the page never goes down because one check failed.

---

## 7. Deployment

- **Local** — `uvicorn app:app` plus `npx @maximhq/bifrost`, two processes.
- **Docker** — `docker compose up --build`; the app and the Bifrost image share a network, `BIFROST_URL=http://bifrost:8080`.
- **Static front end** — served by FastAPI from `static/`; no CDN, no build, no external fonts.

The app holds no secrets in code. `.env` is gitignored; `.env.example` documents every variable. Fixtures contain no credentials.

---

## 8. Known failure modes

| Failure | Effect | Visible as |
| --- | --- | --- |
| Tavily key absent | no live evidence | `mode.evidence = recorded/none`, chip "Recorded evidence", disclaimer |
| Tavily returns nothing for a claim | that claim is unverified | claim status `unverified`, source count 0 |
| Bifrost down | direct provider call | `mode.judge = direct`, chip "Direct · Groq", `llm_error` |
| No provider key | recorded or heuristic judging | `mode.judge = recorded/heuristic`, chip "No model" |
| Model returns malformed JSON | retry once, then heuristic | `mode.judge = heuristic` or claim `uncertain` |
| Sample edited | evidence is not reused | `sample_matched = false`, `mode.evidence = none` |
| Undated sources | neutral recency | `recency = 0.55`, source shows "undated" |

These are not edge cases hidden from the user; they are the states the interface is designed to display.
