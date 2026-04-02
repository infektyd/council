#!/usr/bin/env python3
"""
conductor.py — Council orchestrator.

Routes tasks to the optimal persona, calls the bridge, generates transcripts,
posts to Discord, notifies the main seat, and emits a structured JSON result
envelope on stdout for machine-readable handoff.

Usage:
    python conductor.py --mode verdict "Your detailed prompt here."
    python conductor.py --mode deliberation "Your prompt here."
    python conductor.py --dry-run "Test prompt."
"""

# ---------------------------------------------------------------------------
# EPHEMERAL BREADCRUMB — remove when stale
# Current objective: keep council genuinely usable for binary work.
# Current direction: stdout/envelope stays authoritative; Discord is human
# visibility; notify.py must wake or at least clearly report why it could not.
# Next proof gate: one real run where transcript writes, Discord posts final
# output, and main-seat notify survives transient lock/gateway stalls.
# Key artifacts / paths:
#   - scripts/notify.py
#   - scripts/discord.py
#   - scripts/logs/council.log
#   - scripts/council_transcript_*.md
# ---------------------------------------------------------------------------

import argparse
import json
import sys
import time
import threading
from datetime import datetime

# Ensure scripts/ is on the path for sibling imports
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    MODELS, PERSONAS, CLASSIFICATION_RULES,
    DISCORD_DRY_RUN, OPENCLAW_DRY_RUN,
    log, preflight, SCRIPTS_DIR,
    DELIBERATION_MODEL_OVERRIDES,
)
from bridge import call_model, BridgeResult
import transcript
import discord
import notify


# ---------------------------------------------------------------------------
# Task classification
# ---------------------------------------------------------------------------

def classify_task(task: str) -> str:
    """
    Classify a task into a persona based on keyword matching.

    Scoring: each keyword match adds 1 point. The persona with the highest
    score wins. Ties go to workhorse (most general-purpose). If no keywords
    match, defaults to workhorse.
    """
    desc = task.lower()
    scores: dict[str, int] = {}

    for persona, keywords in CLASSIFICATION_RULES.items():
        score = sum(1 for kw in keywords if kw in desc)
        if score > 0:
            scores[persona] = score

    if not scores:
        return "workhorse"

    # Return highest-scoring persona; workhorse wins ties
    max_score = max(scores.values())
    for persona in ["workhorse", "creative", "speed"]:
        if scores.get(persona, 0) == max_score:
            return persona

    return max(scores, key=scores.get)


# ---------------------------------------------------------------------------
# Verdict mode — single persona, single round
# ---------------------------------------------------------------------------

def run_verdict(task: str) -> dict:
    """Route to one persona and return its response as the verdict."""
    persona = classify_task(task)
    model_slug = MODELS[persona][0]

    log.info(f"VERDICT mode: classified as {persona.upper()} -> {model_slug}")
    _progress(f"Routing to {persona.upper()} ({model_slug})...")

    result = call_model(task, persona=persona)

    if not result.ok:
        return _error_envelope("verdict", persona, result.error)

    # Generate transcript
    rounds = [{
        "persona": persona,
        "model": result.model,
        "latency_s": result.latency_s,
        "text": result.text,
        "ok": True,
    }]

    transcript_path = transcript.generate(
        task=task,
        mode="verdict",
        rounds=rounds,
        verdict=result.text,
        verdict_persona=persona,
        total_latency_s=result.latency_s,
    )

    # Discord + notify (best-effort, non-blocking for the envelope)
    discord_ok = discord.post(result.text, task, transcript_path)
    notify_ok = notify.notify_main_seat(
        task=task,
        transcript_path=transcript_path,
        output=result.text,
        mode="verdict",
        routed_to=persona,
    )

    return _ok_envelope(
        mode="verdict",
        routed_to=persona,
        model=result.model,
        transcript_path=transcript_path,
        verdict=result.text,
        rounds=rounds,
        discord_posted=discord_ok,
        main_seat_notified=notify_ok,
        total_latency_s=result.latency_s,
    )


# ---------------------------------------------------------------------------
# Deliberation mode — multiple personas, then conductor synthesizes
# ---------------------------------------------------------------------------

DELIBERATION_ORDER = ["workhorse", "creative", "speed"]

def run_deliberation(task: str) -> dict:
    """
    Run WORKHORSE, CREATIVE, and SPEED in parallel on the same task,
    then have CONDUCTOR synthesize a final verdict from all three.
    Supports model diversity via environment variables:
        COUNCIL_DELIBERATION_WORKHORSE_MODEL
        COUNCIL_DELIBERATION_CREATIVE_MODEL
        COUNCIL_DELIBERATION_SPEED_MODEL
    """
    t0 = time.monotonic()
    progress_lock = threading.Lock()
    threads = []
    results = {}  # persona -> BridgeResult

    def worker(persona: str):
        model_slug, is_multi = MODELS[persona]
        override = DELIBERATION_MODEL_OVERRIDES.get(persona)
        # Call model with override if set
        result = call_model(task, persona=persona, model_override=override)
        with progress_lock:
            if result.ok:
                _progress(f"  {persona.upper()} done ({result.latency_s:.1f}s)")
            else:
                _progress(f"  {persona.upper()} FAILED: {result.error[:100]}")
        results[persona] = result

    # Launch threads for each persona
    for persona in DELIBERATION_ORDER:
        model_slug, is_multi = MODELS[persona]
        override = DELIBERATION_MODEL_OVERRIDES.get(persona)
        display_model = override if override else model_slug
        with progress_lock:
            _progress(f"Calling {persona.upper()} ({display_model})...")
        t = threading.Thread(target=worker, args=(persona,))
        t.start()
        threads.append(t)

    # Wait for all threads to complete
    for t in threads:
        t.join()

    t1 = time.monotonic()
    # Build rounds and collect successful outputs
    rounds: list[dict] = []
    persona_outputs: dict[str, str] = {}
    for persona in DELIBERATION_ORDER:
        result = results.get(persona)
        if result is None:
            # Should not happen, but guard
            continue
        round_data = {
            "persona": persona,
            "model": result.model,
            "latency_s": result.latency_s,
            "text": result.text if result.ok else f"[ERROR] {result.error}",
            "ok": result.ok,
        }
        rounds.append(round_data)
        if result.ok:
            persona_outputs[persona] = result.text

    # Build synthesis prompt for the conductor
    if not persona_outputs:
        return _error_envelope("deliberation", "conductor",
                               "All personas failed. No input for synthesis.")

    synthesis_prompt = _build_synthesis_prompt(task, persona_outputs)

    _progress("Calling CONDUCTOR for synthesis...")
    conductor_result = call_model(synthesis_prompt, persona="conductor")

    t2 = time.monotonic()
    total_latency = t2 - t0

    if conductor_result.ok:
        verdict = conductor_result.text
        verdict_persona = "conductor"
        rounds.append({
            "persona": "conductor",
            "model": conductor_result.model,
            "latency_s": conductor_result.latency_s,
            "text": conductor_result.text,
            "ok": True,
        })
    else:
        # Fallback: use the workhorse output if conductor fails
        verdict = persona_outputs.get("workhorse",
                   persona_outputs.get(next(iter(persona_outputs)), "[no output]"))
        verdict_persona = "workhorse (conductor fallback)"
        rounds.append({
            "persona": "conductor",
            "model": conductor_result.model,
            "latency_s": conductor_result.latency_s,
            "text": f"[CONDUCTOR FAILED] {conductor_result.error}",
            "ok": False,
        })
        _progress(f"  CONDUCTOR failed, falling back to WORKHORSE output.")

    transcript_path = transcript.generate(
        task=task,
        mode="deliberation",
        rounds=rounds,
        verdict=verdict,
        verdict_persona=verdict_persona,
        total_latency_s=total_latency,
    )

    discord_ok = discord.post(verdict, task, transcript_path)
    notify_ok = notify.notify_main_seat(
        task=task,
        transcript_path=transcript_path,
        output=verdict,
        mode="deliberation",
        routed_to=verdict_persona,
    )

    return _ok_envelope(
        mode="deliberation",
        routed_to=verdict_persona,
        model=MODELS.get("conductor", ("unknown",))[0],
        transcript_path=transcript_path,
        verdict=verdict,
        rounds=rounds,
        discord_posted=discord_ok,
        main_seat_notified=notify_ok,
        total_latency_s=round(total_latency, 2),
    )


def run_deliberation_v3(task: str) -> dict:
    """
    V3 Enhanced deliberation with structured factor outputs.
    Personas output both prose AND structured JSON in one response.
    """
    from council_upgrades import (
        get_structured_prompt, extract_dual_response, 
        perspectives_to_v3_format, run_upgraded_deliberation
    )
    
    t0 = time.monotonic()
    progress_lock = threading.Lock()
    threads = []
    results = {}  # persona -> BridgeResult

    def worker(persona: str):
        # Build structured prompt for this persona
        persona_desc = PERSONAS.get(persona, "A wise advisor.")
        if isinstance(persona_desc, tuple):
            persona_desc = persona_desc[0]
        structured_prompt = get_structured_prompt(
            persona_name=persona.upper(),
            persona_description=persona_desc,
            topic=task
        )
        
        override = DELIBERATION_MODEL_OVERRIDES.get(persona)
        result = call_model(structured_prompt, persona=persona, model_override=override)
        with progress_lock:
            if result.ok:
                _progress(f"  {persona.upper()} done ({result.latency_s:.1f}s)")
            else:
                _progress(f"  {persona.upper()} FAILED: {result.error[:100]}")
        results[persona] = result

    # Launch threads for each persona
    for persona in DELIBERATION_ORDER:
        model_slug, is_multi = MODELS[persona]
        override = DELIBERATION_MODEL_OVERRIDES.get(persona)
        display_model = override if override else model_slug
        with progress_lock:
            _progress(f"Calling {persona.upper()} ({display_model}) with structured prompt...")
        t = threading.Thread(target=worker, args=(persona,))
        t.start()
        threads.append(t)

    # Wait for all threads to complete
    for t in threads:
        t.join()

    t1 = time.monotonic()
    
    # Extract structured perspectives from responses
    structured_perspectives = []
    rounds = []
    prose_outputs = {}
    
    for persona in DELIBERATION_ORDER:
        result = results.get(persona)
        if result is None:
            continue
        
        round_data = {
            "persona": persona,
            "model": result.model,
            "latency_s": result.latency_s,
            "text": result.text if result.ok else f"[ERROR] {result.error}",
            "ok": result.ok,
        }
        rounds.append(round_data)
        
        if result.ok:
            structured = extract_dual_response(result.text, persona.upper())
            structured_perspectives.append(structured)
            prose_outputs[persona] = structured.prose_perspective

    # Run V3 analysis on structured data
    if structured_perspectives:
        v3_data = perspectives_to_v3_format(structured_perspectives)
        v3_result = run_upgraded_deliberation(task, v3_data)
    else:
        v3_result = {"error": "No successful persona responses"}

    # Build synthesis prompt for conductor (uses prose)
    if not prose_outputs:
        return _error_envelope("deliberation", "conductor",
                               "All personas failed. No input for synthesis.")

    synthesis_prompt = _build_synthesis_prompt(task, prose_outputs)

    _progress("Calling CONDUCTOR for synthesis...")
    conductor_result = call_model(synthesis_prompt, persona="conductor")

    t2 = time.monotonic()
    total_latency = t2 - t0

    if conductor_result.ok:
        verdict = conductor_result.text
        verdict_persona = "conductor"
        rounds.append({
            "persona": "conductor",
            "model": conductor_result.model,
            "latency_s": conductor_result.latency_s,
            "text": verdict,
            "ok": True,
        })
    else:
        verdict = f"[ERROR] Conductor synthesis failed: {conductor_result.error}"
        verdict_persona = "conductor"
        rounds.append({
            "persona": "conductor",
            "model": conductor_result.model,
            "latency_s": conductor_result.latency_s,
            "text": verdict,
            "ok": False,
        })

    # Generate transcript
    transcript_path = transcript.generate(
        task=task,
        mode="deliberation_v3",
        rounds=rounds,
        verdict=verdict,
        verdict_persona=verdict_persona,
        total_latency_s=total_latency,
    )

    # Post to Discord
    discord_ok = discord.post(verdict, task, transcript_path)

    # Notify main seat
    notify_ok = notify.notify_main_seat(
        task=task,
        transcript_path=transcript_path,
        output=verdict,
        mode="deliberation",
        routed_to=verdict_persona,
    )

    # Combine verdict with V3 analysis
    combined_result = {
        **_ok_envelope(
            mode="deliberation_v3",
            routed_to=verdict_persona,
            model=MODELS.get("conductor", ("unknown",))[0],
            transcript_path=transcript_path,
            verdict=verdict,
            rounds=rounds,
            discord_posted=discord_ok,
            main_seat_notified=notify_ok,
            total_latency_s=round(total_latency, 2),
        ),
        "v3_analysis": v3_result,
        "structured_perspectives": [
            {
                "persona": sp.persona_name,
                "stance": sp.overall_stance,
                "confidence": sp.confidence,
                "factors_count": len(sp.key_factors),
                "cruxes_count": len(sp.cruxes),
            }
            for sp in structured_perspectives
        ],
    }
    
    return combined_result


def _build_synthesis_prompt(task: str, outputs: dict[str, str]) -> str:
    """Build the synthesis prompt for the conductor from individual outputs."""
    sections = []
    for persona, text in outputs.items():
        sections.append(f"### {persona.upper()} Response\n\n{text}")

    joined = "\n\n---\n\n".join(sections)

    return (
        f"You are synthesizing a council deliberation.\n\n"
        f"## Original Task\n\n{task}\n\n"
        f"## Council Member Responses\n\n{joined}\n\n"
        f"## Your Job\n\n"
        f"Read all responses above. Identify the strongest ideas, "
        f"resolve any contradictions, fill gaps, and produce a single "
        f"coherent, high-quality final answer to the original task. "
        f"Credit specific council members where appropriate. "
        f"Your output IS the council's final verdict."
    )


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------

def _ok_envelope(
    mode: str,
    routed_to: str,
    model: str,
    transcript_path: str,
    verdict: str,
    rounds: list[dict],
    discord_posted: bool,
    main_seat_notified: bool,
    total_latency_s: float,
) -> dict:
    # Make transcript path relative for portability
    rel_path = transcript.relative_path(transcript_path)

    return {
        "status": "ok",
        "mode": mode,
        "routed_to": routed_to,
        "model": model,
        "transcript_path": rel_path,
        "summary": verdict[:300],
        "verdict": verdict,
        "discord_posted": discord_posted,
        "main_seat_notified": main_seat_notified,
        "total_latency_s": round(total_latency_s, 2),
        "rounds": len(rounds),
        "timestamp": datetime.now().isoformat(),
    }


def _error_envelope(mode: str, routed_to: str, error: str) -> dict:
    return {
        "status": "error",
        "error": error,
        "mode": mode,
        "routed_to": routed_to,
        "timestamp": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Progress output (human-readable, goes to stderr so stdout stays clean)
# ---------------------------------------------------------------------------

def _progress(msg: str):
    """Print human-readable progress to stderr."""
    print(f"[council] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Council Conductor — multi-agent orchestration for OpenClaw",
    )
    parser.add_argument(
        "--mode",
        choices=["verdict", "deliberation"],
        default="verdict",
        help="Operating mode (default: verdict)",
    )
    parser.add_argument(
        "--v3",
        action="store_true",
        help="Enable V3 enhanced deliberation with structured factors, heatmap, cruxes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run classification and report routing without calling the API",
    )
    parser.add_argument(
        "--persona",
        choices=list(MODELS.keys()),
        default=None,
        help="Force a specific persona (bypasses classifier)",
    )
    parser.add_argument(
        "prompt",
        nargs="+",
        help="The task/prompt for the council",
    )

    args = parser.parse_args()
    task = " ".join(args.prompt)

    # Pre-flight check
    problems = preflight()
    if problems and not args.dry_run:
        envelope = _error_envelope(
            mode=args.mode,
            routed_to="none",
            error=f"Pre-flight failed: {'; '.join(problems)}",
        )
        print(json.dumps(envelope))
        sys.exit(1)

    # Dry run: classify and report
    if args.dry_run:
        persona = args.persona or classify_task(task)
        model_slug = MODELS[persona][0]
        _progress(f"DRY RUN — would route to {persona.upper()} ({model_slug})")
        _progress(f"Mode: {args.mode}")
        envelope = {
            "status": "dry_run",
            "mode": args.mode,
            "routed_to": persona,
            "model": model_slug,
            "task_preview": task[:200],
        }
        print(json.dumps(envelope))
        sys.exit(0)

    # Real execution
    _progress(f"Council starting in {args.mode.upper()} mode{' (V3)' if args.v3 else ''}...")

    if args.mode == "deliberation":
        if args.v3:
            envelope = run_deliberation_v3(task)
        else:
            envelope = run_deliberation(task)
    else:
        # Verdict mode — allow persona override
        if args.persona:
            # Direct persona call
            _progress(f"Forced persona: {args.persona.upper()}")
            result = call_model(task, persona=args.persona)
            if result.ok:
                rounds = [{
                    "persona": args.persona,
                    "model": result.model,
                    "latency_s": result.latency_s,
                    "text": result.text,
                    "ok": True,
                }]
                tp = transcript.generate(
                    task=task, mode="verdict", rounds=rounds,
                    verdict=result.text, verdict_persona=args.persona,
                    total_latency_s=result.latency_s,
                )
                d_ok = discord.post(result.text, task, tp)
                n_ok = notify.notify_main_seat(
                    task=task, transcript_path=tp, output=result.text,
                    mode="verdict", routed_to=args.persona,
                )
                envelope = _ok_envelope(
                    mode="verdict", routed_to=args.persona, model=result.model,
                    transcript_path=tp, verdict=result.text, rounds=rounds,
                    discord_posted=d_ok, main_seat_notified=n_ok,
                    total_latency_s=result.latency_s,
                )
            else:
                envelope = _error_envelope("verdict", args.persona, result.error)
        else:
            envelope = run_verdict(task)

    # Emit the structured envelope on stdout (last line)
    _progress("Council complete.")
    print(json.dumps(envelope))

    if envelope.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()