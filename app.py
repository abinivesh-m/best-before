"""Best Before - every AI answer gets an expiry date."""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bb import config, fixtures, llm, verify

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bestbefore")

app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    description="Detects stale claims in AI answers and re-verifies them against live web sources.",
)
app.mount("/static", StaticFiles(directory=str(config.STATIC)), name="static")


class CheckRequest(BaseModel):
    text: str = Field(min_length=12, max_length=config.MAX_TEXT)
    sample_id: str | None = Field(default=None, max_length=80)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(config.STATIC / "index.html")


@app.get("/api/health")
async def health() -> dict:
    gateway = await llm.gateway_status()
    return {
        "ok": True,
        "name": config.APP_NAME,
        "version": config.APP_VERSION,
        "modes": {
            "bifrost": gateway["reachable"],
            "direct": bool(config.GROQ_KEY),
            "tavily": config.tavily_ready(),
            "recorded_evidence": bool(fixtures.samples()),
        },
        "gateway": gateway,
        "thresholds": {"current_at": config.CURRENT_AT, "stale_below": config.STALE_BELOW},
        "recorded_on": fixtures.recorded_on(),
    }


@app.get("/api/samples")
async def samples() -> dict:
    rows = []
    for sample in fixtures.samples():
        rows.append(
            {
                "id": sample["id"],
                "title": sample["title"],
                "written": sample.get("written"),
                "blurb": sample.get("blurb", ""),
                "claims": len(sample.get("claims", [])),
                "answer": sample.get("answer", ""),
            }
        )
    return {
        "samples": rows,
        "recorded_on": fixtures.recorded_on(),
        "capture": fixtures.capture_note(),
    }


@app.get("/api/gateway")
async def gateway() -> dict:
    status = await llm.gateway_status()
    return {
        **status,
        "fallbacks": config.BIFROST_FALLBACKS,
        "direct_model": config.GROQ_MODEL if config.GROQ_KEY else "",
        "direct_ready": bool(config.GROQ_KEY),
    }


@app.post("/api/check")
async def check(request: CheckRequest) -> dict:
    text = request.text.strip()
    if len(text) < 12:
        raise HTTPException(status_code=400, detail="Give me at least a sentence to check.")
    try:
        report = await verify.check(text, request.sample_id)
    except Exception as exc:  # a broken check must not take the page down
        log.exception("check failed")
        raise HTTPException(status_code=500, detail=f"check failed: {type(exc).__name__}") from exc
    return {"ok": True, "report": report}


@app.get("/api/docs", include_in_schema=False)
async def api_docs_redirect():
    return FileResponse(config.STATIC / "index.html")


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
