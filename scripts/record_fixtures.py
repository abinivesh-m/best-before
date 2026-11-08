"""Refresh the recorded evidence in fixtures/samples.json from live Tavily search.

The fixtures that ship with the app were captured by hand from the cited pages.
This script does the same job automatically: for every claim in every sample it
runs the claim's query through Tavily, asks the configured model to judge each
result, and rewrites the sample's sources with what it found.

    python scripts/record_fixtures.py --dry-run
    python scripts/record_fixtures.py            # writes fixtures/samples.json

It needs TAVILY_API_KEY. It uses Bifrost when it is running, otherwise the
direct provider, then a heuristic judge, exactly like the app does.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bb import config, fixtures, llm, scoring, tavily, verify  # noqa: E402


async def capture(claim: dict) -> list[dict]:
    result = await tavily.search(claim["query"])
    if result.mode != "tavily":
        print(f"    ! {claim['id']}: {result.error or 'no results'}")
        return []

    verdict = await verify._judge_with_model(claim, result.results)
    if verdict is not None:
        judged, _current, _note, _model = verdict
        method = "model"
    else:
        judged = verify.heuristic_judge(claim, result.results)
        method = "heuristic"

    print(f"    {claim['id']}: {len(judged)} sources via {method} judge")
    today = date.today()
    out = []
    for source in judged:
        stance = source.get("stance", "unrelated")
        if stance not in ("supports", "contradicts", "unrelated"):
            stance = "unrelated"
        out.append(
            {
                "title": source.get("title") or source.get("url", ""),
                "url": source.get("url", ""),
                "content": source.get("content", "")[:1200],
                "published_date": source.get("published_date"),
                "score": round(float(source.get("score", 0.6)), 3),
                "stance": stance,
                "match": round(float(source.get("match", 0.0)), 2),
            }
        )
        # Keep only sources that actually settled the claim; context rows add noise.
    settled = [s for s in out if s["stance"] != "unrelated"]
    return settled or out


async def main(dry_run: bool, only: str | None) -> int:
    if not config.tavily_ready():
        print("TAVILY_API_KEY is not set. Add it to .env and try again.")
        return 2

    path = config.FIXTURES / "samples.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    today = date.today().isoformat()
    touched = 0

    for sample in data["samples"]:
        if only and sample["id"] != only:
            continue
        print(f"\n{sample['id']}")
        for claim in sample["claims"]:
            sources = await capture(claim)
            if not sources:
                continue
            claim["sources"] = sources
            scored = scoring.score_claim(claim, sources, date.fromisoformat(today))
            print(f"    -> {scored['status']} ({scored['score']}/100)")
            touched += 1

    if dry_run:
        print(f"\ndry run: {touched} claims would be updated, nothing written")
        return 0

    data["recorded_on"] = today
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {path} · {touched} claims refreshed · recorded_on={today}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print results without writing")
    parser.add_argument("--sample", help="refresh one sample id only")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dry_run, args.sample)))
