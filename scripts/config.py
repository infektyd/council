"""
config.py — Centralized configuration for the Council skill.

Loads environment variables with fallbacks, validates required values,
and provides the canonical source of truth for all modules.
"""

import os
import re
import sys
import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPTS_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Load config.env if it exists (simple key=value, no shell expansion)
_env_file = SKILL_DIR / "config.env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Don't overwrite existing env vars (env takes precedence)
            if key and key not in os.environ:
                os.environ[key] = value

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("COUNCIL_LOG_LEVEL", "info").upper()
_level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
              "WARNING": logging.WARNING, "ERROR": logging.ERROR}
logging.basicConfig(
    level=_level_map.get(LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "council.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("council")

# ---------------------------------------------------------------------------
# xAI API Key — multi-source resolution
# ---------------------------------------------------------------------------
XAI_API_KEY: str | None = os.environ.get("XAI_API_KEY")

if not XAI_API_KEY:
    _key_path = Path.home() / ".xai-key"
    if _key_path.exists():
        XAI_API_KEY = _key_path.read_text().strip()

if not XAI_API_KEY:
    _conf = Path.home() / ".openclaw" / "openclaw.json"
    if _conf.exists():
        _match = re.search(r'"apiKey":\s*"(xai-[^"]+)"', _conf.read_text())
        if _match:
            XAI_API_KEY = _match.group(1)

# ---------------------------------------------------------------------------
# xAI API settings
# ---------------------------------------------------------------------------
XAI_API_URL = "https://api.x.ai/v1/responses"
MAX_RETRIES = int(os.environ.get("COUNCIL_MAX_RETRIES", "2"))
API_TIMEOUT = int(os.environ.get("COUNCIL_API_TIMEOUT", "180"))

# ---------------------------------------------------------------------------
# Model registry — persona → (model_slug, is_multi_agent)
#
# Slugs verified against xAI console as of 2026-03-15:
#   grok-4.20-multi-agent-beta-0309            2M ctx, 642 rpm, $2/Mtok
#   grok-4.20-multi-agent-experimental-beta-0304  DEPRECATED 2026-03-16, removed from public API
#   grok-4.20-beta-0309-reasoning              2M ctx, 642 rpm, $2/Mtok
#   grok-4.20-beta-0309-non-reasoning          2M ctx, 642 rpm, $2/Mtok
#   grok-code-fast-1                           256K ctx, 2250 rpm, $0.20/Mtok
#   grok-4-1-fast-reasoning                    2M ctx, 642 rpm, $0.20/Mtok
#   grok-4-1-fast-non-reasoning                2M ctx, 642 rpm, $0.20/Mtok
#
# For multi-agent models, agent count is controlled via reasoning.effort:
#   low/medium = 4 agents, high/xhigh = 16 agents (REST API).
#   The agent_count param is xAI SDK only; REST ignores it.
# ---------------------------------------------------------------------------
MODELS = {
    "workhorse": ("grok-4.20-beta-0309-reasoning", False),
    "creative":  ("grok-4.20-multi-agent-beta-0309", True),
    "speed":     ("grok-4-1-fast-reasoning", False),
    "conductor": ("grok-4.20-multi-agent-beta-0309", True),
}

# ---------------------------------------------------------------------------
# Deliberation model overrides (environment variables)
#   COUNCIL_DELIBERATION_WORKHORSE_MODEL
#   COUNCIL_DELIBERATION_CREATIVE_MODEL
#   COUNCIL_DELIBERATION_SPEED_MODEL
# If set, overrides the model used for that persona in deliberation mode.
# ---------------------------------------------------------------------------
DELIBERATION_MODEL_OVERRIDES = {}
for persona in ["workhorse", "creative", "speed"]:
    env_var = f"COUNCIL_DELIBERATION_{persona.upper()}_MODEL"
    override = os.environ.get(env_var)
    if override:
        DELIBERATION_MODEL_OVERRIDES[persona] = override

# ---------------------------------------------------------------------------
# Persona system prompts
# ---------------------------------------------------------------------------
PERSONAS = {
    "workhorse": (
        "You are the Workhorse — deep-focus technical reasoning. "
        "You are methodical, precise, and thorough. You excel at sustained "
        "analysis, architecture design, debugging complex systems, and "
        "producing detailed, correct, well-structured output. "
        "Always show your reasoning. Cite specifics. Never hand-wave."
    ),
    "creative": (
        "You are the Creative Explorer — divergent, high-energy thinking. "
        "You generate novel ideas, unexpected connections, wild concepts, "
        "and unconventional solutions. You are allowed to be bold, weird, "
        "and provocative. Push boundaries. Surprise the reader. "
        "But ground your chaos in enough detail to be actionable."
    ),
    "speed": (
        "You are the Speed Runner — fast, concise, high-signal. "
        "You are excellent at rapid answers, summaries, quick fixes, "
        "lookups, and getting to the point immediately. "
        "No preamble, no filler. Answer directly and move on."
    ),
    "conductor": (
        "You are the Conductor — the synthesizer and final arbiter. "
        "You receive outputs from multiple council members and produce "
        "a unified, coherent verdict. Identify the strongest ideas, "
        "resolve contradictions, fill gaps, and deliver a clear "
        "recommendation. Your word is final."
    ),
}

# ---------------------------------------------------------------------------
# Task classification keywords (persona → keyword list)
# ---------------------------------------------------------------------------
CLASSIFICATION_RULES: dict[str, list[str]] = {
    "workhorse": [
        "analyze", "architecture", "refactor", "debug", "optimize", "scale",
        "review", "audit", "deep", "complex", "large", "sustained", "research",
        "implement", "migrate", "performance", "security", "test", "diagnose",
    ],
    "creative": [
        "creative", "idea", "novel", "design", "brainstorm", "wild", "explore",
        "concept", "story", "art", "imagine", "invent", "diverge", "chaos",
        "unhinged", "experimental", "fun", "weird",
    ],
    "speed": [
        "quick", "fast", "simple", "small", "lookup", "check", "summary",
        "fix", "one-liner", "rapid", "short", "brief", "tldr", "status",
    ],
}

# ---------------------------------------------------------------------------
# Discord configuration
# ---------------------------------------------------------------------------
DISCORD_WEBHOOK_URL = os.environ.get("COUNCIL_DISCORD_WEBHOOK_URL", "")
DISCORD_GUILD_ID = os.environ.get("COUNCIL_DISCORD_GUILD_ID", "")
DISCORD_FORUM_CHANNEL_ID = os.environ.get("COUNCIL_DISCORD_FORUM_ID", "")
DISCORD_CHANNEL_ID = os.environ.get("COUNCIL_DISCORD_CHANNEL_ID", "")
DISCORD_THREAD_ID = os.environ.get("COUNCIL_DISCORD_THREAD_ID", "")
DISCORD_MENTION_USER_ID = os.environ.get("COUNCIL_DISCORD_MENTION_USER_ID", "")
DISCORD_MAX_MSG_LEN = 1900
DISCORD_DRY_RUN = os.environ.get("COUNCIL_DISCORD_DRY_RUN", "").lower() in {"1", "true", "yes"}

# ---------------------------------------------------------------------------
# OpenClaw main-seat notification
#
# Installed OpenClaw builds in this workspace require an explicit target.
# Use a specific session id when you have it; otherwise default to the main
# agent name so `openclaw agent` does not fail with "choose a session".
# ---------------------------------------------------------------------------
OPENCLAW_DRY_RUN = os.environ.get("COUNCIL_OPENCLAW_DRY_RUN", "").lower() in {"1", "true", "yes"}
OPENCLAW_SESSION_ID = os.environ.get("COUNCIL_SESSION_ID", "")
OPENCLAW_AGENT = os.environ.get("COUNCIL_OPENCLAW_AGENT", "main")

# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def preflight() -> list[str]:
    """Return a list of problems. Empty list = all clear."""
    problems: list[str] = []
    if not XAI_API_KEY:
        problems.append("XAI_API_KEY not found in env, ~/.xai-key, or ~/.openclaw/openclaw.json")
    if not DISCORD_WEBHOOK_URL and not DISCORD_THREAD_ID and not DISCORD_CHANNEL_ID and not DISCORD_FORUM_CHANNEL_ID:
        problems.append("No Discord delivery target configured (thread, channel, forum, or webhook)")
    if not OPENCLAW_SESSION_ID and not OPENCLAW_AGENT:
        problems.append("No OpenClaw notify target configured (set COUNCIL_SESSION_ID or COUNCIL_OPENCLAW_AGENT)")
    return problems
