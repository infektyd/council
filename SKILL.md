---
name: council
description: >-
  Multi-agent orchestration via xAI Grok models. Invoked when the user says
  "consult the council", "run a council", "ask the multi-agent", "council review",
  or when a task requires deep multi-perspective reasoning, architectural debate,
  code review, or creative divergence that benefits from parallel agent work.
  Returns a structured result envelope the main seat can parse and continue from.
metadata:
  author: syntra
  version: '2.1'
---

# Council — Multi-Agent Orchestration Skill

## Purpose

The Council is a local multi-agent orchestration system that calls xAI Grok models
through the `/v1/responses` API. It routes tasks to specialized personas, collects
their output, optionally synthesizes a final verdict, posts results to Discord for
human visibility, and hands a structured result envelope back to the calling
OpenClaw seat so execution can continue without ambiguity.

## When to Use This Skill

Use this skill when:
- The user explicitly asks to "consult the council", "run a council", or similar.
- A task benefits from multiple perspectives (architecture, code review, brainstorm).
- Deep, sustained reasoning is needed beyond what a single model pass provides.
- Creative divergence or "outside the box" thinking is requested.

Do NOT use this skill for:
- Simple lookups, quick fixes, or tasks a single model handles fine.
- Anything that does not benefit from multi-agent deliberation.

## Architecture

```
council/
├── SKILL.md              ← You are here. Agent-facing instructions.
├── config.env            ← Environment variable template (copy to .env).
├── README.md             ← Human-facing documentation.
└── scripts/
    ├── config.py          ← Centralized configuration, env loading, validation.
    ├── bridge.py          ← xAI API wrapper. Handles auth, retry, response parsing.
    ├── conductor.py       ← Orchestrator. Routes, calls bridge, synthesizes, returns envelope.
    ├── discord.py         ← Discord delivery (webhook or OpenClaw CLI). Supplemental.
    ├── notify.py          ← Main-seat notification via OpenClaw CLI.
    ├── transcript.py      ← Markdown transcript generation.
    ├── validate.py        ← Pre-flight health check and dry-run script.
    └── logs/              ← Runtime logs (auto-created).
```

## Operating Modes

The conductor supports two modes, selected via the `--mode` flag:

### `verdict` (default)
Synchronous, single-round. One persona is selected based on task classification.
The persona's response IS the council verdict. Best for most tasks.

### `deliberation`
Multi-round with **parallel execution** and **model diversity**:
- Multiple personas each produce a response **in parallel** (cutting latency by ~3x).
- Supports **model diversity** via environment variables: you can specify different
  model versions per persona slot (e.g., workhorse uses `grok-4-0709`, creative uses
  `grok-4.20-multi-agent-experimental-beta-0304`, speed uses `grok-4-1-fast-reasoning`).
- The Conductor persona then synthesizes a final verdict from all inputs.
- Use for tasks that genuinely benefit from multiple viewpoints (architecture decisions,
  tradeoff analysis, creative brainstorming) where latency and model disagreement
  add value.

## How to Invoke

### Step 1 — Prepare context
If the user wants the council to review code or a document, read the relevant
file(s) and include their content in the prompt string. The council has no
filesystem access; everything it needs must be in the prompt.

### Step 2 — Run the conductor synchronously
```bash
python3 ~/.openclaw/workspace/skills/council/scripts/conductor.py \
  --mode verdict \
  "Your detailed prompt here, including any code or context."
```

Or for multi-persona deliberation with parallel execution:
```bash
python3 ~/.openclaw/workspace/skills/council/scripts/conductor.py \
  --mode deliberation \
  "Your detailed prompt here."
```

### Step 2a — Optional: Configure model diversity for deliberation
Set environment variables to override the model used for each persona in deliberation mode:
```bash
# Example: Use different model versions for each council member
COUNCIL_DELIBERATION_WORKHORSE_MODEL=grok-4-0709 \
COUNCIL_DELIBERATION_CREATIVE_MODEL=grok-4.20-multi-agent-experimental-beta-0304 \
COUNCIL_DELIBERATION_SPEED_MODEL=grok-4-1-fast-reasoning \
python3 ~/.openclaw/workspace/skills/council/scripts/conductor.py \
  --mode deliberation \
  "Your detailed prompt here."
```

### Step 3 — Read the result envelope from stdout
The conductor prints a JSON result envelope as its LAST line of stdout.
Everything before that line is human-readable progress output (ignore it for
machine parsing). The envelope looks like:

```json
{
  "status": "ok",
  "mode": "verdict",
  "routed_to": "workhorse",
  "model": "grok-4-0709",
  "transcript_path": "scripts/council_transcript_20260315_0700.md",
  "summary": "First 300 chars of the final output...",
  "verdict": "The full final output text from the council.",
  "discord_posted": true,
  "main_seat_notified": true
}
```

If an error occurred:
```json
{
  "status": "error",
  "error": "Description of what went wrong.",
  "mode": "verdict",
  "routed_to": "workhorse"
}
```

### Step 4 — Continue from the envelope
- If `status` is `"ok"`, read `verdict` for the council's full output. Use it to
  answer the user, continue work, or feed into the next step.
- If `status` is `"error"`, report the error to the user and suggest retrying or
  falling back to a direct approach.
- The `transcript_path` is relative to the skill directory. Read it if the user
  wants the full multi-persona transcript.
- Discord posting and main-seat notification happen automatically. They are
  supplemental visibility — do NOT wait for or depend on them.
- Prefer native Discord routing first: thread reply → channel send → forum post.
  Treat webhook as fallback only.

## Critical Operating Rules

1. **Run synchronously.** Do not background the conductor process.
2. **Treat stdout as the authoritative result.** The JSON envelope on the last
   line is the machine-readable contract. Discord posts are for human eyes only.
3. **Do not parse Discord** for the council's output. Ever.
4. **Include all context in the prompt.** The council cannot read files, browse
   the web, or access OpenClaw tools. Inline everything it needs.
5. **Prefer `verdict` mode** unless the user explicitly asks for multi-persona
   deliberation or the task clearly benefits from it.
6. **If the conductor fails**, check `validate.py` output first:
   ```bash
   python3 ~/.openclaw/workspace/skills/council/scripts/validate.py
   ```

## Personas

| Persona      | Model Slug                                    | Strengths                                          |
|-------------|-----------------------------------------------|----------------------------------------------------|
| WORKHORSE   | `grok-4.20-beta-0309-reasoning`               | Sustained technical reasoning, architecture, debug |
| CREATIVE    | `grok-4.20-multi-agent-beta-0309`             | Divergent thinking, novel ideas, creative chaos    |
| SPEED       | `grok-4-1-fast-reasoning`                     | Rapid answers, summaries, small fixes, lookups     |
| CONDUCTOR   | `grok-4.20-multi-agent-beta-0309`             | Synthesis, final verdicts, tiebreaking            |

In `verdict` mode, the task classifier selects one persona. In `deliberation`
mode, WORKHORSE, CREATIVE, and SPEED each respond in parallel, then CONDUCTOR
synthesizes. Model diversity can be configured via environment variables as
described above.

## Environment Variables

All configuration is in `config.env` (or environment variables). Required:
- `XAI_API_KEY` — xAI API key. Also checks `~/.xai-key` and `~/.openclaw/openclaw.json`.

Optional (with sensible defaults):
- `COUNCIL_DISCORD_THREAD_ID` — Highest-priority native target: reply to a specific thread.
- `COUNCIL_DISCORD_CHANNEL_ID` — Next native fallback: post to this channel via OpenClaw CLI.
- `COUNCIL_DISCORD_FORUM_ID` — Last native fallback: create a forum post via OpenClaw CLI.
- `COUNCIL_DISCORD_WEBHOOK_URL` — Webhook fallback only when native Discord routing is unavailable or fails.
- `COUNCIL_DISCORD_GUILD_ID` — Discord guild ID.
- `COUNCIL_DISCORD_MENTION_USER_ID` — User to @mention in Discord posts.
- `COUNCIL_DISCORD_DRY_RUN` — Set to `1` to skip actual Discord posts.
- `COUNCIL_OPENCLAW_DRY_RUN` — Set to `1` to skip main-seat notification.
- `COUNCIL_SESSION_ID` — Preferred explicit OpenClaw session target when known.
- `COUNCIL_OPENCLAW_AGENT` — Default OpenClaw agent target when no session id is supplied (`main` by default).
- `COUNCIL_LOG_LEVEL` — `debug`, `info`, `warning`, `error` (default: `info`).
- `COUNCIL_MAX_RETRIES` — API retry count (default: `2`).
- `COUNCIL_API_TIMEOUT` — Seconds to wait for xAI response (default: `180`).
- `COUNCIL_DELIBERATION_WORKHORSE_MODEL` — Override model for workhorse in deliberation mode.
- `COUNCIL_DELIBERATION_CREATIVE_MODEL` — Override model for creative in deliberation mode.
- `COUNCIL_DELIBERATION_SPEED_MODEL` — Override model for speed in deliberation mode.
