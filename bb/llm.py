"""Model access.

Every model call tries Bifrost first. Bifrost is an OpenAI-compatible gateway, so
the request body is the same one a provider SDK would send, plus ``fallbacks``.
If the gateway is not running we fall back to the provider directly and label the
mode in the response - the UI never claims "Bifrost" when it was not used.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from . import config


@dataclass
class LLMResult:
    data: dict | None = None
    mode: str = "offline"          # bifrost | direct | offline
    provider: str = ""
    model: str = ""
    is_fallback: bool = False
    latency_ms: float = 0.0
    error: str = ""
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.data is not None


def _json_from(text: str) -> dict | None:
    """Parse JSON even when a model wraps it in prose or a fenced block."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else None
        except Exception:
            return None
    return None


def _routing(payload: dict) -> tuple[str, str, bool, float]:
    """Read Bifrost's routing metadata without depending on one exact shape."""
    extra = payload.get("extra_fields") or {}
    if not isinstance(extra, dict):
        extra = {}
    info = extra.get("routing_info") or payload.get("routing_info") or {}
    if isinstance(info, list):
        info = info[-1] if info else {}
    if not isinstance(info, dict):
        info = {}
    provider = str(info.get("provider") or "")
    model = str(info.get("model") or "")
    is_fallback = bool(info.get("is_fallback"))
    try:
        latency = float(info.get("latency") or 0.0)
    except (TypeError, ValueError):
        latency = 0.0
    return provider, model, is_fallback, latency


def _body(model: str, system: str, user: str, max_tokens: int, temperature: float) -> dict:
    return {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


# A failed gateway should not cost one timeout per claim. Cool down, then retry.
_gateway_down_until = 0.0
_GATEWAY_COOLDOWN_S = 30.0


async def _call(url: str, key: str, body: dict, timeout: float) -> tuple[dict | None, str]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=body)
    if response.status_code != 200:
        detail = response.text[:300].replace("\n", " ")
        return None, f"HTTP {response.status_code}: {detail}"
    payload = response.json()
    try:
        content = payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return None, "response had no choices[0].message.content"
    parsed = _json_from(content)
    if parsed is None:
        return None, "model did not return a JSON object"
    parsed["_raw_routing"] = _routing(payload)
    return parsed, ""


async def complete_json(
    system: str,
    user: str,
    *,
    max_tokens: int = 1400,
    temperature: float = 0.0,
) -> LLMResult:
    """Ask for a JSON object, preferring the gateway."""
    global _gateway_down_until

    started = time.perf_counter()
    gateway_error = ""

    if config.BIFROST_URL and time.monotonic() >= _gateway_down_until:
        body = _body(config.BIFROST_MODEL, system, user, max_tokens, temperature)
        if config.BIFROST_FALLBACKS:
            body["fallbacks"] = config.BIFROST_FALLBACKS
        try:
            data, error = await _call(
                f"{config.BIFROST_URL}/v1/chat/completions",
                config.BIFROST_KEY,
                body,
                config.LLM_TIMEOUT,
            )
        except Exception as exc:  # connection refused, DNS, timeout
            data, error = None, f"{type(exc).__name__}: {exc}"
            _gateway_down_until = time.monotonic() + _GATEWAY_COOLDOWN_S

        if data is not None:
            provider, model, is_fallback, route_ms = data.pop("_raw_routing", ("", "", False, 0.0))
            return LLMResult(
                data=data,
                mode="bifrost",
                provider=provider or "groq",
                model=model or config.BIFROST_MODEL,
                is_fallback=is_fallback,
                latency_ms=round(route_ms or (time.perf_counter() - started) * 1000, 1),
            )
        gateway_error = error

    if not config.GROQ_KEY:
        return LLMResult(
            mode="offline",
            error=gateway_error or "no model provider configured",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    body = _body(config.GROQ_MODEL, system, user, max_tokens, temperature)
    try:
        data, error = await _call(config.GROQ_URL, config.GROQ_KEY, body, config.LLM_TIMEOUT)
    except Exception as exc:
        data, error = None, f"{type(exc).__name__}: {exc}"

    if data is None:
        return LLMResult(
            mode="offline",
            error=f"gateway: {gateway_error or 'skipped'}; direct: {error}",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    data.pop("_raw_routing", None)
    return LLMResult(
        data=data,
        mode="direct",
        provider="groq",
        model=config.GROQ_MODEL,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        error=f"gateway unavailable ({gateway_error})" if gateway_error else "",
    )


async def gateway_status() -> dict:
    """Probe the gateway so the UI can tell the truth about the current mode."""
    status = {
        "url": config.BIFROST_URL,
        "model": config.BIFROST_MODEL,
        "fallbacks": config.BIFROST_FALLBACKS,
        "reachable": False,
        "error": "",
        "direct_provider": "groq" if config.GROQ_KEY else "",
    }
    if not config.BIFROST_URL:
        status["error"] = "BIFROST_URL is empty"
        return status
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            response = await client.get(f"{config.BIFROST_URL}/v1/models")
        status["reachable"] = response.status_code < 500
        if not status["reachable"]:
            status["error"] = f"HTTP {response.status_code}"
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status
