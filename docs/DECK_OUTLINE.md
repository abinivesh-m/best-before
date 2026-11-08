# Best Before — pitch deck outline

Twelve slides. Designed for a five-minute pitch plus three minutes of Q&A. Every number below comes from the shipped code and fixtures; nothing is invented.

---

## 1. Title

**Best Before — Every AI answer gets an expiry date.**

- Subtitle: freshness verification for generated text.
- One line: paste an AI answer, get a freshness score, an expiry date, and the exact passages that went stale.
- Footer: `github.com/abinivesh-m/best-before`

## 2. The problem

- Models answer in the present tense from a snapshot of the past.
- A 2023 guide says "Python 3.12 is the latest release" with total confidence. Three major versions have shipped since.
- The reader has no signal that the sentence rotted. There is no expiry date on a paragraph.
- Teams ship stale answers into docs, runbooks, agents, and customer support because nothing flags which lines expired.

**Hook line:** "The answer is not wrong when you read it. It goes wrong later, and nobody notices."

## 3. What we built

- Paste any AI-generated answer, or pick one of three demo answers.
- Best Before splits it into up to eight factual claims.
- For each claim it searches current sources (Tavily) and asks a model to judge support vs contradiction (Bifrost).
- Output: a freshness score out of 100, a verdict (current / recheck / expired / needs correction), a best-before date, and per-claim evidence.

**Demo line:** "Six of eight claims in this 2023 setup guide are past their best before."

## 4. Demo (live)

1. Pick "The 2023 setup guide".
2. Show the stamp: **EXPIRED · 38/100**.
3. Click claim 1: "Python 3.12 is the latest stable release."
4. Show the contradiction: "Python 3.15.2 is the current release (25 Oct 2026)" with `python.org` authority 0.96.
5. Show the trace: read → claims → evidence → judge → score, with timings.
6. Switch sample to "EU AI Act briefing": **NEEDS CORRECTION · 67/100**. Four dates right, two scope claims wrong.

**Timing:** 90 seconds. Do not explain the formula yet.

## 5. How the score is built

- One claim: `freshness = 0.60·agreement + 0.25·recency + 0.15·authority`
- Agreement is what the sources *say about the specific value*. A single 0.96-authority contradiction beats five blog posts that repeat the claim.
- Recency decays on a half-life by claim type: pricing 120 days, versions 240, regulation 365, company 180.
- Authority is a visible domain tier: primary docs 0.95–0.98, vendor releases 0.86–0.90, curated trackers 0.82, press 0.66–0.78, community 0.22–0.55.
- Thresholds: ≥0.72 current, 0.45–0.72 recheck, <0.45 stale.
- Answer-level: a stale high-impact claim (pricing, version, regulation) can expire the whole answer even if the rest is fine.

**Why these weights:** agreement matters more than recency, because a fresh blog repeating an old version number is still wrong.

## 6. Tavily integration (load-bearing)

- Tavily is the only source of live evidence. Each claim gets its own targeted query.
- We use `content` (the snippet) and `published_date` (the date) — exactly the two fields the scorer needs.
- `search_depth: advanced`, `include_published_date: true`.
- Normalisation in `bb/tavily.py`: clamps relevance to [0,1], parses dates from several formats, discards non-HTTP URLs.
- No key means no live evidence. The report says so instead of inventing sources.

**Honesty line:** "We never fabricate a source. No key = no evidence = `unverified`, never `current`."

## 7. Bifrost integration (load-bearing)

- Bifrost is the model gateway for *both* extraction and judgement. Every call is OpenAI-shaped, plus `fallbacks: ["groq/qwen/qwen3-32b"]` in the request body.
- We read `extra_fields.routing_info` back: provider, model, fallback flag, gateway latency. The UI prints the routed model and marks a fallback route.
- If the gateway is down, we cool down for 30s, call the provider directly, and label the report `direct`.
- "Bifrost gateway" is shown only when a gateway response was actually observed.

**Why it matters:** this is not a mention. The fallback model is in the request body. The routing metadata is on screen.

## 8. Modes are always labelled

- `mode.evidence`: `tavily` / `recorded` / `none`
- `mode.judge`: `bifrost` / `direct` / `recorded` / `heuristic`
- `mode.extraction`: `model` / `recorded` / `heuristic`
- `gateway.used` is a real boolean from observed responses.
- Header chips mirror `/api/health`, so the user sees at a glance whether a check used live search or recorded evidence.

**Honesty line:** "The report may be weaker, but it never claims a stronger mode than what actually ran."

## 9. Recorded evidence, not invented

- Three samples ship with evidence captured 2026-11-08 from cited pages.
- Stored in the same shape Tavily returns, plus stance and match.
- Only reused when the pasted text exactly matches the sample answer. Edit the sample and it comes back `unverified`.
- The verdict banner prints the capture date.

**Why this matters:** the demo works with no keys and no network, and it is still honest.

## 10. Results

- `stack-2023`: 38/100, expired, 6 of 8 stale.
- `pg-upgrade`: 42/100, expired, 3 of 5 stale.
- `ai-act`: 67/100, needs correction, 2 of 6 stale.
- Test suite: 22 passing, no keys, no network.
- API: `/api/health`, `/api/samples`, `/api/gateway`, `/api/check`.

## 11. Limits (what it does not do)

- Freshness is not truth. A current claim can still be misleading; we score agreement with current sources, not correctness in the abstract.
- Recency depends on `published_date`, which many pages omit. Undated sources get a neutral 0.55, not a punishment.
- Tavily snippets are short. The judge is told to answer `unrelated` rather than guess.
- Extraction is capped at eight claims.
- Authority is a small, visible domain table in `bb/scoring.py`, not a learned ranking.

**Why say this:** honesty about limits is part of the product, and it is what makes the score trustworthy.

## 12. Roadmap / ask

- Add Tavily API key to switch the demo to live evidence.
- Ship a browser extension that annotates answers in place.
- Add claim-type detection for pricing pages and API docs.
- Open source: MIT, repo public, tests green.

**Ask:** feedback on the scoring weights and the authority table. Both are small and visible on purpose.

---

## Speaker notes

- Do not say "hackathon" anywhere in the deck or the demo.
- Never present recorded evidence as live. If asked, show the mode chip and the capture date.
- If Bifrost is down during the demo, the report says `direct`. Do not hide it.
- The gauge colour fix and the stamp are the visual hooks; spend five seconds on each.
- End on slide 11, not 12. Limits are the honest close.
