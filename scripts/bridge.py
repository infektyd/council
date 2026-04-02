"""
bridge.py — xAI API wrapper for the Council skill.

Handles authentication, request construction, retry logic, and response
parsing. Provides a clean interface (call_model / BridgeResult) for the
conductor to use without touching HTTP details.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

import config

log = logging.getLogger("council.bridge")


@dataclass
class BridgeResult:
    """Structured result from a single model call."""
    ok: bool
    text: str
    error: str
    model: str
    latency_s: float


def _extract_text(payload: dict) -> str:
    """Extract the response text from an xAI /v1/responses payload."""
    # Responses API format
    output = payload.get("output")
    if isinstance(output, list):
        chunks = []
        for item in output:
            content = item.get("content", []) if isinstance(item, dict) else []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    text = block.get("text", "")
                    if text:
                        chunks.append(text)
        if chunks:
            return "\n".join(chunks).strip()

    # Chat completions fallback
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str) and content.strip():
            return content.strip()

    raise RuntimeError("xAI response did not contain extractable text")


def call_model(
    prompt: str,
    persona: str = "workhorse",
    model_override: Optional[str] = None,
) -> BridgeResult:
    """
    Call the xAI API for a given persona.

    Args:
        prompt: The full prompt text to send.
        persona: One of "workhorse", "creative", "speed", "conductor".
        model_override: Optional model slug to use instead of the persona default.

    Returns:
        BridgeResult with ok=True and text on success, or ok=False and error on failure.
    """
    if not config.XAI_API_KEY:
        return BridgeResult(
            ok=False, text="", error="No API key configured",
            model="", latency_s=0.0,
        )

    # Resolve model
    if model_override:
        model_slug = model_override
        is_multi = "multi-agent" in model_override.lower()
    else:
        model_slug, is_multi = config.MODELS.get(persona, config.MODELS["workhorse"])

    # Build system prompt
    system_prompt = config.PERSONAS.get(persona, config.PERSONAS["workhorse"])

    # Build request body
    body_dict = {
        "model": model_slug,
        "input": prompt,
        "instructions": system_prompt,
    }

    # Multi-agent models use reasoning.effort to control agent count
    if is_multi:
        body_dict["reasoning"] = {"effort": "high"}

    body = json.dumps(body_dict).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {config.XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    last_error = ""
    for attempt in range(1, config.MAX_RETRIES + 2):  # +2 because range is exclusive and we want retries + 1 attempts
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(
                config.XAI_API_URL,
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

            latency = time.monotonic() - t0
            text = _extract_text(payload)

            log.info(
                "OK persona=%s model=%s latency=%.1fs len=%d",
                persona, model_slug, latency, len(text),
            )
            return BridgeResult(
                ok=True, text=text, error="",
                model=model_slug, latency_s=round(latency, 2),
            )

        except urllib.error.HTTPError as exc:
            latency = time.monotonic() - t0
            err_body = exc.read().decode("utf-8", errors="ignore")[:300]
            last_error = f"HTTP {exc.code}: {err_body}"
            log.warning(
                "FAIL attempt=%d/%d persona=%s model=%s error=%s",
                attempt, config.MAX_RETRIES + 1, persona, model_slug, last_error,
            )
            # Retry on 429 (rate limit) and 5xx
            if exc.code == 429 or exc.code >= 500:
                backoff = min(2 ** attempt, 30)
                log.info("Retrying in %ds...", backoff)
                time.sleep(backoff)
                continue
            break  # Don't retry 4xx (except 429)

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            latency = time.monotonic() - t0
            last_error = str(exc)
            log.warning(
                "FAIL attempt=%d/%d persona=%s error=%s",
                attempt, config.MAX_RETRIES + 1, persona, last_error,
            )
            backoff = min(2 ** attempt, 30)
            time.sleep(backoff)
            continue

        except RuntimeError as exc:
            latency = time.monotonic() - t0
            last_error = str(exc)
            log.error("Parse error persona=%s: %s", persona, last_error)
            break

    return BridgeResult(
        ok=False, text="", error=last_error,
        model=model_slug, latency_s=round(time.monotonic() - t0, 2),
    )
