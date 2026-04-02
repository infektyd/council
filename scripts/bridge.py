"""
bridge.py — xAI API bridge with retry, model routing, and structured response parsing.

This module is the only place that talks to the xAI API.
Every other module calls bridge.call_model() and gets back a BridgeResult.
"""

import json
import time
import logging
from dataclasses import dataclass, field

import requests  # type: ignore

from config import (
    XAI_API_KEY, XAI_API_URL, MAX_RETRIES, API_TIMEOUT,
    MODELS, PERSONAS, log,
)

# ---------------------------------------------------------------------------
# Result envelope from the bridge
# ---------------------------------------------------------------------------

@dataclass
class BridgeResult:
    ok: bool
    text: str = ""
    error: str = ""
    model: str = ""
    persona: str = ""
    latency_s: float = 0.0
    raw_response: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "text": self.text[:500] if self.ok else "",
            "error": self.error,
            "model": self.model,
            "persona": self.persona,
            "latency_s": round(self.latency_s, 2),
        }

# ---------------------------------------------------------------------------
# Response parsing — handles the multiple formats xAI returns
# ---------------------------------------------------------------------------

def _parse_response(data: dict) -> str:
    """Extract text from xAI /v1/responses JSON. Handles known variants."""
    # Format 1: output[].content[].text (multi-agent and standard responses)
    if "output" in data:
        outputs = data["output"]
        if isinstance(outputs, list):
            texts = []
            for item in outputs:
                if isinstance(item, dict) and "content" in item:
                    for block in item["content"]:
                        if isinstance(block, dict) and block.get("type") == "output_text":
                            texts.append(block.get("text", ""))
            if texts:
                return "\n".join(texts)
            # Fallback: stringify the output list
            return str(outputs)
        return str(outputs)

    # Format 2: top-level text
    if "text" in data:
        return data["text"]

    # Format 3: OpenAI-compatible choices
    if "choices" in data:
        try:
            return data["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError):
            pass

    return f"[PARSE_ERROR] Unexpected response format: {json.dumps(data, indent=2)[:500]}"

# ---------------------------------------------------------------------------
# Core API call
# ---------------------------------------------------------------------------

def call_model(
    prompt: str,
    persona: str = "workhorse",
    *,
    system_override: str | None = None,
    model_override: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 16384,
    reasoning_effort: str = "high",
) -> BridgeResult:
    """
    Call an xAI Grok model with retry and structured result.

    Parameters
    ----------
    prompt : str
        The user-facing prompt (task + context).
    persona : str
        One of: workhorse, creative, speed, conductor.
    system_override : str, optional
        Replace the default system prompt for this persona.
    model_override : str, optional
        Force a specific model slug instead of the persona default.
    temperature : float
        Sampling temperature (0.0-2.0).
    max_tokens : int
        Max output tokens.
    reasoning_effort : str
        For multi-agent models, this controls agent count via REST API:
          "low" / "medium" = 4 agents
          "high" / "xhigh" = 16 agents
        For single models, this controls reasoning depth.
        Note: agent_count is xAI SDK only; REST API uses reasoning.effort.
    """
    if not XAI_API_KEY:
        return BridgeResult(ok=False, error="XAI_API_KEY not configured", persona=persona)

    # Resolve model and system prompt
    model_slug, is_multi = MODELS.get(persona, MODELS["workhorse"])
    if model_override:
        model_slug = model_override
        is_multi = "multi-agent" in model_override

    system_prompt = system_override or PERSONAS.get(persona, PERSONAS["workhorse"])

    # Build payload
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    payload: dict = {
        "model": model_slug,
        "input": messages,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }

    if reasoning_effort and is_multi:
        payload["reasoning"] = {"effort": reasoning_effort}

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Retry loop
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 2):  # +2 because range is exclusive and attempt 1 is the first try
        t0 = time.monotonic()
        try:
            log.info(f"Bridge call attempt {attempt}/{MAX_RETRIES + 1}: "
                     f"persona={persona} model={model_slug}")

            resp = requests.post(
                XAI_API_URL,
                json=payload,
                headers=headers,
                timeout=API_TIMEOUT,
            )

            latency = time.monotonic() - t0

            # Rate limit — back off and retry
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "10"))
                log.warning(f"Rate limited. Sleeping {retry_after}s before retry.")
                time.sleep(retry_after)
                last_error = f"Rate limited (429). Retried after {retry_after}s."
                continue

            # Server error — retry
            if resp.status_code >= 500:
                log.warning(f"Server error {resp.status_code}. Retrying.")
                time.sleep(3 * attempt)
                last_error = f"Server error {resp.status_code}: {resp.text[:200]}"
                continue

            # Client error (4xx) — do not retry (except 429 above)
            if resp.status_code >= 400:
                error_detail = resp.text[:500]
                log.error(f"Client error {resp.status_code}: {error_detail}")
                return BridgeResult(
                    ok=False,
                    error=f"HTTP {resp.status_code}: {error_detail}",
                    model=model_slug,
                    persona=persona,
                    latency_s=latency,
                )

            # Success
            data = resp.json()
            text = _parse_response(data)

            if text.startswith("[PARSE_ERROR]"):
                return BridgeResult(
                    ok=False,
                    error=text,
                    model=model_slug,
                    persona=persona,
                    latency_s=latency,
                    raw_response=data,
                )

            return BridgeResult(
                ok=True,
                text=text,
                model=model_slug,
                persona=persona,
                latency_s=latency,
                raw_response=data,
            )

        except requests.exceptions.Timeout:
            latency = time.monotonic() - t0
            log.warning(f"Timeout after {latency:.1f}s on attempt {attempt}.")
            last_error = f"Timeout after {latency:.1f}s"
            continue

        except requests.exceptions.ConnectionError as e:
            latency = time.monotonic() - t0
            log.warning(f"Connection error on attempt {attempt}: {e}")
            last_error = f"Connection error: {e}"
            time.sleep(3 * attempt)
            continue

        except Exception as e:
            latency = time.monotonic() - t0
            log.error(f"Unexpected error on attempt {attempt}: {e}")
            return BridgeResult(
                ok=False,
                error=f"Unexpected error: {e}",
                model=model_slug,
                persona=persona,
                latency_s=latency,
            )

    # All retries exhausted
    return BridgeResult(
        ok=False,
        error=f"All {MAX_RETRIES + 1} attempts failed. Last error: {last_error}",
        model=model_slug,
        persona=persona,
    )


# ---------------------------------------------------------------------------
# CLI entry point for direct testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python bridge.py [--persona PERSONA] \"prompt\"")
        print(f"  Personas: {', '.join(MODELS.keys())}")
        sys.exit(1)

    args = sys.argv[1:]
    persona = "workhorse"
    if "--persona" in args:
        idx = args.index("--persona")
        persona = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    prompt = " ".join(args)
    result = call_model(prompt, persona=persona)

    if result.ok:
        print(result.text)
    else:
        print(f"[ERROR] {result.error}", file=sys.stderr)
        sys.exit(1)
