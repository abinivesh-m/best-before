# Best Before — demo script (silent screen + voiceover)

**Target length:** 90 seconds. Record the screen silently, then lay your ElevenLabs narration over it.

**Recording setup**
1. Make sure Bifrost (`:8080`) and the app (`:8000`) are running.
2. Open `http://127.0.0.1:8000` in Chrome at 1280×900. Close other tabs.
3. Use OBS / Windows Game Bar / any screen recorder. Capture the browser window only.
4. Record at 30 fps, 1080p. Do not capture system audio.
5. Leave ~2 seconds of still frame at the start and end for editing.

**Pacing rule:** wait for each check to finish (the button re-enables) before moving on. A check takes 8–15 seconds. If you are rate-limited, wait 60 seconds and retry — do not record a spinner.

---

## Shot list

### 0:00 – 0:08 · Title card
**On screen:** home page, hero headline, header chips (Recorded/Live evidence · Bifrost gateway).
**Action:** none. Let the page sit.
**Narration:**
> Best Before. Every AI answer gets an expiry date.
> Language models answer in the present tense from a snapshot of the past.

### 0:08 – 0:16 · The problem
**On screen:** still home page.
**Narration:**
> A 2023 setup guide says "Python 3.12 is the latest release" with total confidence.
> Three major versions have shipped since, and nothing in the answer tells you.

### 0:16 – 0:26 · What it does
**On screen:** scroll slightly to show the three sample cards.
**Action:** scroll down ~200px, then back to top.
**Narration:**
> Best Before splits an answer into individual claims, re-verifies each against current sources,
> and gives the whole answer a freshness score and an expiry date.

### 0:26 – 0:34 · Sample 1 verdict
**On screen:** "The 2023 setup guide" is already loaded. Click **Check freshness**. Wait for the result.
**Action:** click Check. Wait ~12s for EXPIRED stamp.
**Narration:**
> Here is a 2023 setup guide. It scores 38 out of 100. Expired.
> Six of eight claims are past their best before.

### 0:34 – 0:46 · A single claim
**On screen:** scroll to the first claim card. Show the contradiction and the source.
**Action:** scroll to claim 1 (Python 3.12). Let the evidence details stay open.
**Narration:**
> Click the first claim: "Python 3.12 is the latest stable release."
> The evidence contradicts it. Python 3.15.2 is current, from python.org, authority 0.96.

### 0:46 – 0:56 · The score
**On screen:** point at the three numbers (agreement / recency / authority) on the claim card.
**Narration:**
> Every claim carries agreement, recency, and authority.
> A single primary-source contradiction outweighs any number of blog posts that repeat the claim.

### 0:56 – 1:06 · The trace
**On screen:** scroll to the pipeline trace at the bottom.
**Narration:**
> The trace shows read, claims, evidence, judge, score, with timings.
> The judge ran through Bifrost, and the routed model is printed on screen.

### 1:06 – 1:16 · Sample 2
**On screen:** click "The EU AI Act briefing" sample, then Check. Wait for result.
**Action:** click sample card, click Check, wait ~15s.
**Narration:**
> Switch to the EU AI Act briefing. 67 out of 100. Needs correction.
> Four dates are right. The scope and compliance-timeline claims are wrong.

### 1:16 – 1:26 · Live mode
**On screen:** click "Clear", paste a short answer, click Check. Wait for result. Show the "Live Tavily search" chip.
**Action:** clear, paste `Python 3.12 is the latest stable release. Node 18 is the current LTS.`, click Check, wait ~15s.
**Narration:**
> Paste any answer and it runs live Tavily search, judged through Bifrost.
> No keys means recorded evidence and an honest label — never a fake "current".

### 1:26 – 1:32 · Limits
**On screen:** scroll to the "How it works" / limits section, or back to the verdict.
**Narration:**
> Freshness is not truth, and we say so.
> Undated sources get a neutral score, not a punishment.

### 1:32 – 1:36 · Close
**On screen:** back to the hero verdict (EXPIRED stamp).
**Narration:**
> Best Before. Every AI answer gets an expiry date.

---

## Recording tips
- Keep the mouse still when a result appears; let the viewer read.
- If a check is slow, cut the dead time in post — the trace still shows the real timings.
- Do not record system audio. Add the ElevenLabs track in your editor.
- Keep the header chips visible in every shot; they are the honesty proof.

## Files to reference
- `docs/screenshot-hero.png` — the verdict panel (38/100 EXPIRED).
- `docs/screenshot-full.png` — full page with claim cards.
- `docs/screenshot-mobile.png` — mobile layout.
- `docs/DECK_OUTLINE.md` — slide-by-slide pitch.
