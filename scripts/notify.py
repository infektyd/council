"""
notify.py — Main-seat notification via OpenClaw CLI.

This is the agent wake-up path. After the council completes, this module
sends a message into the agent's own session so the calling agent knows
work is done and can continue.

The correct CLI command for this build is an explicitly targeted one:
    openclaw agent --agent main --message "..."        (default main agent)
    openclaw agent --session-id <id> --message "..."   (specific session)

NOT openclaw message send --channel session (that's for outbound channels only).
Discord is for human visibility; this is for agent continuation.
"""

# ---------------------------------------------------------------------------
# EPHEMERAL BREADCRUMB — remove when stale
# Current objective: make main-seat notify reliable enough that short runtime
# stalls do not look like hard council failure.
# Current direction: keep the proven `openclaw agent` interface, retry only on
# transient gateway/session-lock signatures, and avoid inventing alternate
# notify channels without proof.
# Next proof gate: a live notify succeeds after one or more transient retries,
# or council.log proves the remaining blocker is host/runtime outside the skill.
# Key artifacts / paths:
#   - scripts/logs/council.log
#   - scripts/config.py (target selection)
#   - config.env (COUNCIL_SESSION_ID / COUNCIL_OPENCLAW_AGENT)
# ---------------------------------------------------------------------------

import subprocess
import time

from config import (
    OPENCLAW_DRY_RUN,
    OPENCLAW_SESSION_ID,
    OPENCLAW_AGENT,
    DISCORD_THREAD_ID,
    DISCORD_CHANNEL_ID,
    DISCORD_FORUM_CHANNEL_ID,
    log,
)
from transcript import relative_path


_NOTIFY_ATTEMPTS = 3
_NOTIFY_RETRY_DELAY_S = 5
_NOTIFY_TIMEOUT_S = 30


def _is_transient_notify_failure(detail: str) -> bool:
    text = (detail or "").lower()
    return any(token in text for token in [
        "gateway closed",
        "gateway connect failed",
        "session file locked",
        "timeout 10000ms",
    ])


def notify_main_seat(
    task: str,
    transcript_path: str,
    output: str,
    mode: str,
    routed_to: str,
) -> bool:
    """
    Send a completion message into the main OpenClaw agent session.

    Uses an explicitly targeted `openclaw agent` command so the CLI does not
    fail with "choose a session" on builds that require a target selector.

    Returns True on success, False on failure.
    """
    if OPENCLAW_DRY_RUN:
        log.info("Main seat notify: DRY RUN (skipped)")
        return True

    transcript_rel = relative_path(transcript_path)

    # Build a concise preview
    preview = output.strip()
    if len(preview) > 800:
        preview = preview[:760] + "\n...[truncated; see transcript]"

    # Discord location hint
    if DISCORD_THREAD_ID:
        discord_hint = f"Discord thread `{DISCORD_THREAD_ID}`"
    elif DISCORD_CHANNEL_ID:
        discord_hint = f"Discord channel `{DISCORD_CHANNEL_ID}`"
    elif DISCORD_FORUM_CHANNEL_ID:
        discord_hint = f"Discord forum `{DISCORD_FORUM_CHANNEL_ID}`"
    else:
        discord_hint = "Discord (no target configured)"

    msg = (
        "Council finished. Wake and review.\n\n"
        f"Results posted to {discord_hint}.\n"
        f"Local transcript: `{transcript_rel}`\n\n"
        f"Mode: {mode}\n"
        f"Routed to: {routed_to.upper()}\n"
        f"Task: {task[:200]}\n\n"
        "Preview:\n"
        f"{preview}\n\n"
        "Review the result and decide how to continue."
    )

    # Build the correct command with an explicit target.
    cmd = ["openclaw", "agent"]

    if OPENCLAW_SESSION_ID:
        cmd.extend(["--session-id", OPENCLAW_SESSION_ID])
    elif OPENCLAW_AGENT:
        cmd.extend(["--agent", OPENCLAW_AGENT])
    else:
        log.error("Main seat notify failed: no OpenClaw target configured")
        return False

    cmd.extend(["--message", msg])

    for attempt in range(1, _NOTIFY_ATTEMPTS + 1):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=_NOTIFY_TIMEOUT_S)
        except FileNotFoundError:
            log.error("Main seat notify failed: 'openclaw' CLI not found in PATH")
            return False
        except Exception as e:
            log.error(f"Main seat notify failed: {e}")
            return False

        if result.returncode == 0:
            log.info(f"Main seat notified via 'openclaw agent' (attempt {attempt}/{_NOTIFY_ATTEMPTS})")
            if result.stdout.strip():
                log.debug(f"Agent response: {result.stdout.strip()[:200]}")
            return True

        detail = result.stderr.strip() or result.stdout.strip() or "unknown failure"
        if attempt < _NOTIFY_ATTEMPTS and _is_transient_notify_failure(detail):
            log.warning(
                f"Main seat notify transient failure on attempt {attempt}/{_NOTIFY_ATTEMPTS}: {detail[:250]}"
            )
            time.sleep(_NOTIFY_RETRY_DELAY_S)
            continue

        log.error(f"Main seat notify failed (exit {result.returncode}): {detail}")
        return False

    return False
