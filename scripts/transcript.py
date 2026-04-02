"""
transcript.py — Structured Markdown transcript generation.

Generates a physical transcript file for every council session.
Transcripts are human-readable and include routing decisions,
persona responses, timing, and the final verdict.
"""

import os
from datetime import datetime
from pathlib import Path

from config import SCRIPTS_DIR, log


def generate(
    task: str,
    mode: str,
    rounds: list[dict],
    verdict: str,
    verdict_persona: str,
    total_latency_s: float,
) -> str:
    """
    Write a Markdown transcript and return its absolute path.

    Parameters
    ----------
    task : str
        The original task/prompt.
    mode : str
        "verdict" or "deliberation".
    rounds : list[dict]
        Each dict has: persona, model, latency_s, text, ok.
    verdict : str
        The final synthesized output.
    verdict_persona : str
        Which persona produced (or synthesized) the verdict.
    total_latency_s : float
        Wall-clock time for the entire session.
    """
    ts = datetime.now()
    filename = f"council_transcript_{ts.strftime('%Y%m%d_%H%M%S')}.md"
    filepath = SCRIPTS_DIR / filename

    lines: list[str] = []
    lines.append(f"# Council Transcript — {ts.isoformat()}")
    lines.append("")
    lines.append(f"**Mode:** {mode}")
    lines.append(f"**Task:** {task}")
    lines.append(f"**Total latency:** {total_latency_s:.1f}s")
    lines.append(f"**Verdict by:** {verdict_persona.upper()}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, r in enumerate(rounds, 1):
        persona = r.get("persona", "unknown").upper()
        model = r.get("model", "unknown")
        latency = r.get("latency_s", 0)
        ok = r.get("ok", False)
        text = r.get("text", "[no response]")
        status = "OK" if ok else "FAILED"

        lines.append(f"## Round {i}: {persona} ({model})")
        lines.append(f"**Status:** {status} | **Latency:** {latency:.1f}s")
        lines.append("")
        lines.append(text)
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Final Verdict")
    lines.append("")
    lines.append(verdict)
    lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Transcript written: {filepath}")
    return str(filepath)


def relative_path(transcript_path: str) -> str:
    """Return transcript path relative to the skill directory for display."""
    try:
        return os.path.relpath(transcript_path, str(Path.home() / ".openclaw" / "workspace"))
    except Exception:
        return transcript_path
