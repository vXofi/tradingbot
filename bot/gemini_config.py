"""
Gemini model selection for the free-tier API.

Only Flash / Flash-Lite models with an active free tier (Google docs, 2026).
Legacy gemini-2.0-* removed — API often reports limit=0 (free tier disabled).

Override with GEMINI_MODEL in .env (see .env.example).
"""

from __future__ import annotations

import os
from typing import Optional

# Documented free-tier models (May 2026). No gemini-2.0-* — deprecated / limit=0.
FREE_TIER_MODEL_CANDIDATES: tuple[str, ...] = (
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
)


def get_gemini_model_candidates(explicit: Optional[str] = None) -> list[str]:
    """Return ordered model IDs to try."""
    if explicit and explicit.strip():
        return [explicit.strip()]
    env_model = (os.getenv("GEMINI_MODEL") or "").strip()
    if env_model:
        return [env_model]
    return list(FREE_TIER_MODEL_CANDIDATES)


def classify_gemini_error(exc: Exception, model: str) -> str:
    msg = str(exc)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        if "limit: 0" in msg:
            return (
                f"{model}: no free-tier quota in this region "
                f"(API limit=0 — policy, not your usage)"
            )
        return f"{model}: rate limit / daily quota exhausted (429)"
    if "API_KEY_INVALID" in msg or "API key not valid" in msg:
        return "invalid GEMINI_API_KEY"
    if "User location is not supported" in msg or "FAILED_PRECONDITION" in msg:
        return f"{model}: region not supported"
    if "404" in msg or "NOT_FOUND" in msg:
        return f"{model}: model not found"
    return f"{model}: {type(exc).__name__} — {msg[:120]}"


def _summarize_errors(errors: list[str]) -> str:
    region = [e.split(":")[0] for e in errors if "region not supported" in e]
    no_free = [e.split(":")[0] for e in errors if "limit=0" in e or "no free-tier quota" in e]
    quota_hit = [e for e in errors if "429" in e and "limit=0" not in e and "no free-tier" not in e]
    other = [e for e in errors if e not in [x for x in errors if "region" in x or "free-tier" in x or "429" in x]]

    parts: list[str] = []
    if region:
        parts.append(f"region blocked: {', '.join(region)}")
    if no_free:
        parts.append(f"no free tier: {', '.join(no_free)}")
    if quota_hit:
        parts.append(f"quota exhausted: {len(quota_hit)} model(s)")
    if other and not parts:
        parts.append(other[0])

    if not parts:
        return " | ".join(errors)
    return "; ".join(parts)


def probe_gemini(api_key: str, models: Optional[list[str]] = None) -> tuple[bool, str, Optional[str]]:
    """
    Try a minimal generate_content call on each candidate model.

    Returns (success, message, working_model).
    """
    if not api_key.strip():
        return False, "GEMINI_API_KEY not set", None

    try:
        from google import genai
    except ImportError:
        return False, "google-genai not installed", None

    client = genai.Client(api_key=api_key.strip())
    errors: list[str] = []

    for model in models or get_gemini_model_candidates():
        try:
            response = client.models.generate_content(
                model=model,
                contents='Reply with JSON only: {"ok": true}',
            )
            if response.text:
                return True, f"Gemini OK (model={model})", model
            errors.append(f"{model}: empty response")
        except Exception as exc:
            errors.append(classify_gemini_error(exc, model))

    return False, _summarize_errors(errors), None
