"""Configuration, paths, and a dependency-free .env loader."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
FIXTURES = ROOT / "fixtures"
DOCS = ROOT / "docs"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)


def load_env() -> None:
    """Read ROOT/.env into os.environ without overwriting anything already set."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


# --- model gateway (Bifrost) -------------------------------------------------
BIFROST_URL = env("BIFROST_URL", "http://localhost:8080").rstrip("/")
BIFROST_KEY = env("BIFROST_KEY")
BIFROST_MODEL = env("BIFROST_MODEL", "groq/openai/gpt-oss-120b")
BIFROST_FALLBACKS = [
    m.strip() for m in env("BIFROST_FALLBACKS", "groq/qwen/qwen3-32b").split(",") if m.strip()
]

# Direct provider, used only when the gateway is not running. The UI says so.
GROQ_URL = env("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_KEY = env("GROQ_API_KEY")
GROQ_MODEL = env("GROQ_MODEL", "openai/gpt-oss-120b")

LLM_TIMEOUT = float(env("LLM_TIMEOUT", "60"))
LLM_WORKERS = int(env("LLM_WORKERS", "4"))

# --- live web evidence (Tavily) ---------------------------------------------
TAVILY_URL = env("TAVILY_URL", "https://api.tavily.com/search")
TAVILY_KEY = env("TAVILY_API_KEY")
TAVILY_DEPTH = env("TAVILY_DEPTH", "advanced")
TAVILY_RESULTS = int(env("TAVILY_RESULTS", "5"))
TAVILY_TIMEOUT = float(env("TAVILY_TIMEOUT", "30"))

# --- product behaviour -------------------------------------------------------
# Claims at or above CURRENT_AT are "current"; below STALE_BELOW are "stale".
CURRENT_AT = float(env("CURRENT_AT", "0.72"))
STALE_BELOW = float(env("STALE_BELOW", "0.45"))

MAX_CLAIMS = int(env("MAX_CLAIMS", "8"))
MAX_TEXT = int(env("MAX_TEXT", "8000"))

APP_NAME = "Best Before"
APP_VERSION = "1.0.0"


def tavily_ready() -> bool:
    return bool(TAVILY_KEY)


def llm_ready() -> bool:
    return bool(GROQ_KEY)
