"""Thin DeepSeek HTTPS client (OpenAI-compatible) — stdlib only.

Why stdlib instead of `openai` SDK: we want zero new dependencies, and our
usage is one POST endpoint with JSON request + JSON response. Adding a
240-package SDK for that would be silly.

Configuration:
- DEEPSEEK_API_KEY (required)
- DEEPSEEK_BASE_URL (optional, defaults to https://api.deepseek.com)
- DEEPSEEK_MODEL (optional, defaults to "deepseek-v4-flash")

The client returns a `LLMResponse` with the parsed JSON content + token
counts + estimated cost. JSON-mode is enabled (DeepSeek understands
`response_format: {type: "json_object"}` the same way OpenAI does).

Failures bubble up as `LLMError`. Caller decides whether to retry. We do
one internal retry on transient network errors and on JSON-parse failures.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


# ───── Secrets loading ──────────────────────────────────────────────────
# When the dashboard or cron is started by launchd, the user's shell
# profile (which exports DEEPSEEK_API_KEY) is NOT sourced. To keep
# interactive and unattended runs on the same code path, we also read
# from ~/.daytrace/secrets.env (KEY=VALUE per line) as a fallback for
# any DEEPSEEK_* var that isn't already in os.environ.

_SECRETS_PATH = Path.home() / ".daytrace" / "secrets.env"
_secrets_loaded = False


def _load_secrets_into_environ() -> None:
    """Idempotent: populate os.environ from ~/.daytrace/secrets.env without
    overwriting anything that's already set. Silently no-ops when the
    file doesn't exist OR when we're running under pytest (so tests
    that explicitly `monkeypatch.delenv('DEEPSEEK_API_KEY')` aren't
    second-guessed)."""
    global _secrets_loaded
    if _secrets_loaded:
        return
    # Reset on every call when under pytest so monkeypatched envs win,
    # but don't cache the no-op (tests may toggle env between cases).
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    _secrets_loaded = True
    if not _SECRETS_PATH.exists():
        return
    try:
        for line in _SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip(); v = v.strip()
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass

# DeepSeek pricing as of Q2 2026 in USD per 1M tokens (cache-miss path).
# Used only for display; not load-bearing. Update freely.
PRICE_PER_M_INPUT_USD = 0.07
PRICE_PER_M_OUTPUT_USD = 1.10


class LLMError(RuntimeError):
    """Any failure to get a usable JSON response."""


class ShapeError(LLMError):
    """The response was valid JSON but didn't conform to the channel's
    expected shape. Carries a human-readable description used to ask the
    model for a corrective re-emit."""


@dataclass
class LLMResponse:
    json: Any
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def is_available() -> bool:
    """Cheap pre-flight: have an API key set? Also lazily populates env
    from ~/.daytrace/secrets.env so launchd-spawned processes work the
    same as interactive shells."""
    _load_secrets_into_environ()
    return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())


def call_json_validated(
    *,
    system: str,
    user: str,
    validator,
    model: str | None = None,
    temperature: float = 0.4,
    max_tokens: int = 2048,
    timeout: float = 60.0,
    transient_retries: int = 1,
    shape_retries: int = 1,
) -> LLMResponse:
    """Call the model, then validate the JSON shape. On shape failure, retry
    once with a corrective system message that quotes the validator's error.

    `validator(payload)` should raise `ShapeError("explanation")` if the
    shape is wrong, otherwise return the normalized payload (or the same
    payload). The validator is the gatekeeper between LLM output and the
    rest of the pipeline — channels stop accepting freeform shapes here.
    """
    resp = call_json(
        system=system, user=user, model=model, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout, retries=transient_retries,
    )
    try:
        resp.json = validator(resp.json)
        return resp
    except ShapeError as shape_err:
        if shape_retries <= 0:
            raise
        corrective = (
            "你的上一条回复 JSON 结构不符合要求, 原因: " + str(shape_err)
            + "\n\n请严格按以下 JSON shape 重新输出, 只输出 JSON, 不要解释:\n\n"
            + user
        )
        resp2 = call_json(
            system=system, user=corrective, model=model, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout, retries=transient_retries,
        )
        resp2.json = validator(resp2.json)
        # Combine usage from both calls so caller's cost accounting is honest.
        resp2.tokens_in += resp.tokens_in
        resp2.tokens_out += resp.tokens_out
        resp2.cost_usd = round(resp2.cost_usd + resp.cost_usd, 6)
        return resp2


def call_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.4,
    max_tokens: int = 2048,
    timeout: float = 60.0,
    retries: int = 1,
) -> LLMResponse:
    """POST to /chat/completions with JSON response mode. Returns parsed JSON.

    Raises LLMError on any failure after `retries` retries.
    """
    _load_secrets_into_environ()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise LLMError("DEEPSEEK_API_KEY not set in environment")

    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model_name = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
    url = f"{base_url}/chat/completions"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_text = resp.read().decode("utf-8")
            return _parse_response(raw_text, model_name)
        except urllib.error.HTTPError as e:
            # 4xx errors are usually permanent — don't waste retries on them
            err_body = e.read().decode("utf-8", errors="replace")[:500] if hasattr(e, "read") else ""
            if 400 <= e.code < 500:
                raise LLMError(f"HTTP {e.code}: {err_body}") from e
            last_err = LLMError(f"HTTP {e.code}: {err_body}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = LLMError(f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    assert last_err is not None
    raise last_err


def _parse_response(raw_text: str, model_name: str) -> LLMResponse:
    envelope = json.loads(raw_text)
    choices = envelope.get("choices") or []
    if not choices:
        raise LLMError(f"no choices in response: {raw_text[:300]}")
    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise LLMError(f"empty content in response: {raw_text[:300]}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        # Some models wrap JSON in ```json fences. Strip and retry once.
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].lstrip()
            if stripped.endswith("```"):
                stripped = stripped[:-3]
            parsed = json.loads(stripped)
        else:
            raise LLMError(f"model returned non-JSON content: {content[:300]}") from e

    usage = envelope.get("usage") or {}
    tin = int(usage.get("prompt_tokens") or 0)
    tout = int(usage.get("completion_tokens") or 0)
    cost = (tin / 1_000_000) * PRICE_PER_M_INPUT_USD + (tout / 1_000_000) * PRICE_PER_M_OUTPUT_USD
    return LLMResponse(
        json=parsed,
        tokens_in=tin,
        tokens_out=tout,
        cost_usd=round(cost, 6),
        model=str(envelope.get("model") or model_name),
        raw=envelope,
    )
