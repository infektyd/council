#!/usr/bin/env python3
"""
validate.py — Pre-flight health check for the Council skill.

Run this to verify configuration, API connectivity, and module integrity
before invoking the conductor in production.

Usage:
    python validate.py           # Full check
    python validate.py --quick   # Config only, no API call
"""

import argparse
import json
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    XAI_API_KEY, XAI_API_URL, MODELS, PERSONAS,
    DISCORD_WEBHOOK_URL, DISCORD_THREAD_ID, DISCORD_CHANNEL_ID, DISCORD_FORUM_CHANNEL_ID,
    OPENCLAW_SESSION_ID, OPENCLAW_AGENT,
    LOG_DIR, SCRIPTS_DIR,
    preflight, log,
)


def check_config() -> list[str]:
    """Validate all configuration values."""
    issues = preflight()

    # Check that all persona models have system prompts
    for persona in MODELS:
        if persona not in PERSONAS:
            issues.append(f"Persona '{persona}' has a model but no system prompt")

    # Check log directory
    if not LOG_DIR.exists():
        issues.append(f"Log directory does not exist: {LOG_DIR}")

    # Check scripts directory integrity
    required_modules = ["config.py", "bridge.py", "conductor.py",
                        "discord.py", "notify.py", "transcript.py"]
    for mod in required_modules:
        if not (SCRIPTS_DIR / mod).exists():
            issues.append(f"Required module missing: scripts/{mod}")

    # Make the notify target explicit in the report
    if OPENCLAW_SESSION_ID:
        log.info(f"Notify target: session {OPENCLAW_SESSION_ID}")
    elif OPENCLAW_AGENT:
        log.info(f"Notify target: agent {OPENCLAW_AGENT}")

    return issues


def check_api_connectivity() -> list[str]:
    """Make a minimal API call to verify auth and connectivity."""
    issues = []

    if not XAI_API_KEY:
        issues.append("Cannot test API: no key configured")
        return issues

    try:
        import requests
        resp = requests.post(
            XAI_API_URL,
            json={
                "model": MODELS["speed"][0],
                "input": [{"role": "user", "content": "Reply with only the word OK."}],
                "max_output_tokens": 10,
            },
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        if resp.status_code == 200:
            log.info("API connectivity: OK")
        elif resp.status_code == 401:
            issues.append("API key is invalid (401 Unauthorized)")
        elif resp.status_code == 403:
            issues.append(f"API access forbidden (403): {resp.text[:200]}")
        elif resp.status_code == 429:
            log.warning("API rate limited (429) during health check — key is valid")
        else:
            issues.append(f"Unexpected API response: {resp.status_code} {resp.text[:200]}")

    except ImportError:
        issues.append("'requests' library not installed (pip install requests)")
    except Exception as e:
        issues.append(f"API connectivity check failed: {e}")

    return issues


def check_openclaw_cli() -> list[str]:
    """Check if openclaw CLI is available."""
    issues = []
    try:
        result = subprocess.run(
            ["openclaw", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            issues.append(f"openclaw CLI returned non-zero: {result.stderr.strip()}")
        else:
            log.info(f"OpenClaw CLI: {result.stdout.strip()}")
    except FileNotFoundError:
        issues.append("openclaw CLI not found in PATH")
    except Exception as e:
        issues.append(f"openclaw CLI check failed: {e}")

    return issues


def check_discord_delivery() -> list[str]:
    """Dry-run the actual configured Discord route to catch config/routing failures."""
    issues = []

    if DISCORD_THREAD_ID:
        cmd = [
            "openclaw", "message", "thread", "reply",
            "--channel", "discord",
            "--target", DISCORD_THREAD_ID,
            "--message", "Council validate dry-run",
            "--dry-run",
        ]
        label = f"thread reply ({DISCORD_THREAD_ID})"
    elif DISCORD_CHANNEL_ID:
        cmd = [
            "openclaw", "message", "send",
            "--channel", "discord",
            "--target", f"channel:{DISCORD_CHANNEL_ID}",
            "--message", "Council validate dry-run",
            "--dry-run",
        ]
        label = f"channel send ({DISCORD_CHANNEL_ID})"
    elif DISCORD_FORUM_CHANNEL_ID:
        cmd = [
            "openclaw", "message", "thread", "create",
            "--channel", "discord",
            "--target", f"channel:{DISCORD_FORUM_CHANNEL_ID}",
            "--thread-name", "Council validate dry-run",
            "--message", "Council validate dry-run",
            "--dry-run",
        ]
        label = f"forum post ({DISCORD_FORUM_CHANNEL_ID})"
    elif DISCORD_WEBHOOK_URL:
        log.info("Discord delivery target: webhook only (no CLI dry-run available)")
        return issues
    else:
        issues.append("No Discord delivery route configured")
        return issues

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            log.info(f"Discord dry-run OK: {label}")
        else:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown failure"
            issues.append(f"Discord dry-run failed for {label}: {detail}")
    except FileNotFoundError:
        issues.append("Cannot dry-run Discord route: openclaw CLI not found in PATH")
    except Exception as e:
        issues.append(f"Discord dry-run failed for {label}: {e}")

    return issues


def check_notify_config() -> list[str]:
    """Catch the known notify failure mode before production runs."""
    issues = []
    if not OPENCLAW_SESSION_ID and not OPENCLAW_AGENT:
        issues.append("Main-seat notify has no explicit target (set COUNCIL_SESSION_ID or COUNCIL_OPENCLAW_AGENT)")
    return issues


def main():
    parser = argparse.ArgumentParser(description="Council pre-flight validator")
    parser.add_argument("--quick", action="store_true",
                        help="Config check only, skip API and CLI tests")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON instead of human-readable")
    args = parser.parse_args()

    all_issues: dict[str, list[str]] = {}

    # Config checks (always)
    config_issues = check_config()
    if config_issues:
        all_issues["config"] = config_issues

    notify_issues = check_notify_config()
    if notify_issues:
        all_issues["notify"] = notify_issues

    if not args.quick:
        # API check
        api_issues = check_api_connectivity()
        if api_issues:
            all_issues["api"] = api_issues

        # OpenClaw CLI check
        cli_issues = check_openclaw_cli()
        if cli_issues:
            all_issues["openclaw_cli"] = cli_issues

        # Discord route check
        discord_issues = check_discord_delivery()
        if discord_issues:
            all_issues["discord"] = discord_issues

    # Report
    if args.json:
        report = {
            "status": "fail" if all_issues else "ok",
            "issues": all_issues,
            "checks_run": ["config", "notify"] + (["api", "openclaw_cli", "discord"] if not args.quick else []),
        }
        print(json.dumps(report, indent=2))
    else:
        if all_issues:
            print("PREFLIGHT ISSUES FOUND:\n")
            for category, issues in all_issues.items():
                print(f"  [{category.upper()}]")
                for issue in issues:
                    print(f"    - {issue}")
                print()
            print("Fix these issues before running the conductor.")
        else:
            scope = "config + notify" if args.quick else "config + notify + API + CLI + Discord dry-run"
            print(f"All checks passed ({scope}). Council is ready.")

    sys.exit(1 if all_issues else 0)


if __name__ == "__main__":
    main()
