"""Local LLM client — standalone replacement for morning-brief dependency.

Provides the same interface that analyzer._call_llm expects:
  LLMProvider, call_with_fallback

Reads DEEPSEEK_API_KEY from environment or .env file.
"""

from __future__ import annotations
import json
import logging
import os
import time
import urllib.request
import urllib.error

logger = logging.getLogger("deep_report.llm_client")

# Default DeepSeek endpoint (OpenAI-compatible)
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# Load .env if present
_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                if key not in os.environ:
                    os.environ[key] = val


class LLMProvider:
    """Lightweight provider config matching morning-brief's LLMProvider."""

    def __init__(self, endpoint: str, model: str, api_key: str, label: str,
                 extra: dict | None = None):
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.label = label
        self.extra = extra or {}


def call_with_fallback(
    providers: list[LLMProvider],
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4000,
    temperature: float = 0.3,
    timeout: int = 120,
) -> str | None:
    """Call LLM providers in order, falling back on failure."""
    for provider in providers:
        try:
            logger.info("Calling %s (%s)...", provider.label, provider.model)
            result = _call_openai_compatible(
                endpoint=provider.endpoint,
                model=provider.model,
                api_key=provider.api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                extra=provider.extra,
            )
            if result:
                logger.info("  %s OK (%d chars)", provider.label, len(result))
                return result
            logger.warning("  %s returned empty response", provider.label)
        except Exception as e:
            logger.warning("  %s failed: %s", provider.label, e)
            continue

    logger.error("All providers failed")
    return None


def _call_openai_compatible(
    endpoint: str,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4000,
    temperature: float = 0.3,
    timeout: int = 120,
    extra: dict | None = None,
) -> str | None:
    """Call an OpenAI-compatible chat completions endpoint."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # Inject extra params (e.g. thinking.type=disabled for doubao)
    if extra:
        payload.update(extra)

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                result = json.loads(body)
                content = result["choices"][0]["message"]["content"]
                return content
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            if e.code == 429:
                wait = min(2 ** attempt * 5, 60)
                logger.warning("  429 rate limit, retrying in %ds...", wait)
                time.sleep(wait)
                continue
            logger.warning("  HTTP %d: %s", e.code, body[:200])
            return None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            if attempt < 2:
                wait = 2 ** attempt * 2
                logger.warning("  Network error: %s, retrying in %ds...", e, wait)
                time.sleep(wait)
                continue
            logger.warning("  Network error after retries: %s", e)
            return None

    return None


# Exports for backward compat with morning-brief config imports
LLM_MODEL = DEEPSEEK_MODEL


def get_deepseek_api_key() -> str:
    """Get DeepSeek API key from environment."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError(
            "DEEPSEEK_API_KEY not set. "
            "Create .env file with DEEPSEEK_API_KEY=sk-..."
        )
    return key


# ARK/doubao stubs (not configured, but analyzer expects them)
ARK_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
LLM_ENDPOINT = ARK_ENDPOINT


def get_ark_api_key() -> str:
    """Get ARK API key (optional fallback)."""
    return os.environ.get("ARK_API_KEY", "")
