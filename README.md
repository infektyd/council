# Council — Multi-Agent Orchestration Skill for OpenClaw

A local skill that lets your OpenClaw agent delegate tasks to a council of
xAI Grok model personas. The council deliberates, produces a structured
result, posts to Discord for human visibility, and hands a machine-readable
envelope back to the calling agent.

## Quick Start

1. **Configure:** Copy `config.env` and set `XAI_API_KEY` (or put it in `~/.xai-key`). For reliable main-seat notify, set either `COUNCIL_SESSION_ID` or leave `COUNCIL_OPENCLAW_AGENT=main`.
2. **Validate:** `python3 scripts/validate.py`
3. **Run:** `python3 scripts/conductor.py "Your task here."`

## Architecture

```
SKILL.md            → Agent-facing instructions (OpenClaw reads this)
config.env          → Environment configuration
scripts/
  config.py         → Centralized config loading and validation
  bridge.py         → xAI API wrapper (auth, retry, response parsing)
  conductor.py      → Orchestrator (routing, execution, envelope emission)
  discord.py        → Discord posting (native CLI first, webhook fallback)
  notify.py         → Main-seat notification via explicitly targeted OpenClaw CLI
  transcript.py     → Markdown transcript generation
  validate.py       → Pre-flight health check
  logs/             → Runtime logs
```

## Modes

### Verdict (default)
Single persona answers the task. Best for most requests.
```bash
python3 scripts/conductor.py --mode verdict "Review this code for bugs."
```

### Deliberation
Three personas each respond, then the Conductor synthesizes a final verdict.
```bash
python3 scripts/conductor.py --mode deliberation "Design the auth system."
```

### Dry Run
Classify the task and show routing without calling the API.
```bash
python3 scripts/conductor.py --dry-run "Quick test prompt."
```

## Result Envelope

The conductor's stdout (last line) is always a JSON envelope:

```json
{
  "status": "ok",
  "mode": "verdict",
  "routed_to": "workhorse",
  "model": "grok-4-0709",
  "transcript_path": "scripts/council_transcript_20260315_0700.md",
  "summary": "First 300 chars...",
  "verdict": "Full output text.",
  "discord_posted": true,
  "main_seat_notified": true,
  "total_latency_s": 12.4,
  "rounds": 1,
  "timestamp": "2026-03-15T07:00:00"
}
```

Error case:
```json
{
  "status": "error",
  "error": "Description of failure.",
  "mode": "verdict",
  "routed_to": "workhorse"
}
```

## Personas

| Persona    | Model                                         | Role                              |
|-----------|-----------------------------------------------|-----------------------------------|
| WORKHORSE | `grok-4.20-beta-0309-reasoning`               | Deep technical reasoning (2M ctx) |
| CREATIVE  | `grok-4.20-multi-agent-beta-0309`             | Divergent, novel ideas (4/16 agents) |
| SPEED     | `grok-4-1-fast-reasoning`                     | Fast answers and summaries ($0.20/Mtok) |
| CONDUCTOR | `grok-4.20-multi-agent-experimental-beta-0304`| Synthesis and final verdicts (54K rpm) |

## Design Principles

- **Stdout is the contract.** The JSON envelope is the authoritative result.
- **Discord is supplemental.** Human visibility only. Never parse it for continuation.
- **Prefer native Discord routing.** Use thread → channel → forum first; webhook is fallback only.
- **Synchronous execution.** The calling agent runs the conductor and waits.
- **Fail cleanly.** Errors produce a structured error envelope, not crashes.
- **All context in the prompt.** The council has no filesystem or tool access.
