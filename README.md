# Council — Multi-Agent Orchestration Skill for OpenClaw

An OpenClaw skill that lets your agent delegate tasks to a council of xAI Grok
model personas. The council deliberates in parallel, produces auditable
transcripts, posts to Discord for human visibility, and hands a structured
result envelope back to the calling agent.

> **Minimal scaffolding, not rigid hierarchy.** Agents self-coordinate through
> persona specialization and synthesis — no fixed roles imposed at runtime.
> See [examples/transcripts/](examples/transcripts/) for real council sessions
> including self-upgrading deliberation pipelines and bare-metal assembly review.

## Install

### Option A: Clone into your workspace

```bash
git clone https://github.com/infektyd/council.git ~/.openclaw/workspace/skills/council
```

### Option B: Copy into any OpenClaw skills directory

```bash
# Per-agent (highest precedence)
cp -r council/ <workspace>/skills/council

# Shared across agents
cp -r council/ ~/.openclaw/skills/council
```

### Configure

```bash
cd ~/.openclaw/workspace/skills/council
cp config.env.example config.env
nano config.env  # Set XAI_API_KEY at minimum
```

Your xAI API key can also live in `~/.xai-key` or `~/.openclaw/openclaw.json`.

### Validate

```bash
python3 scripts/validate.py
```

### Dependencies

- Python 3.10+
- `requests` library (`pip install requests`)
- [OpenClaw](https://openclaw.ai) installed and configured
- xAI API key ([console.x.ai](https://console.x.ai))

## Usage

### From your OpenClaw agent

Say "consult the council", "run a council", or "council review" — the skill
triggers automatically via SKILL.md.

### Direct CLI

```bash
# Single persona verdict (default — fast, picks the best persona for the task)
python3 scripts/conductor.py "Review this code for memory leaks."

# Full deliberation (3 personas respond in parallel, then synthesis)
python3 scripts/conductor.py --mode deliberation "Design the auth system."

# Dry run (classify and route without calling the API)
python3 scripts/conductor.py --dry-run "Quick test prompt."
```

## Architecture

```
council/
├── SKILL.md               ← Agent-facing instructions (OpenClaw reads this)
├── README.md              ← You are here
├── INSTALL.md             ← Detailed setup guide
├── config.env.example     ← Configuration template
├── funding.json           ← FLOSS/fund manifest
├── scripts/
│   ├── bridge.py          ← xAI API wrapper (auth, retry, response parsing)
│   ├── conductor.py       ← Orchestrator (routing, parallel execution, envelopes)
│   ├── config.py          ← Centralized config loading and validation
│   ├── discord.py         ← Discord posting (native CLI first, webhook fallback)
│   ├── notify.py          ← Main-seat notification via OpenClaw CLI
│   ├── transcript.py      ← Markdown transcript generation
│   └── validate.py        ← Pre-flight health checks
└── examples/
    ├── council_standalone.py      ← Standalone single-file version
    └── transcripts/               ← 15 real council session transcripts
```

## Modes

### Verdict (default)

Single-round. The conductor classifies the task and routes to the best persona.
The persona's response is the council's verdict. Fast and token-efficient.

### Deliberation

Multi-round with parallel execution across three distinct model tiers:

- **WORKHORSE** (Grok 4.20 Reasoning) — deep single-thread analysis
- **CREATIVE** (Grok 4.20 Multi-Agent) — fans out to 4–16 internal sub-agents per call
- **SPEED** (Grok 4.1 Fast Reasoning) — fast, cheap baseline
- All three fire in parallel, then **CONDUCTOR** (Grok 4.20 Multi-Agent) synthesizes a final verdict
- Per-persona model overrides via environment variables
- Use for architecture decisions, tradeoff analysis, creative brainstorming

## Personas & Model Diversity

Council doesn't just prompt-switch on one model — it orchestrates across three
distinct Grok model tiers simultaneously, each with fundamentally different
cost/capability profiles:

| Persona    | Default Model                           | Tier | Role                                        |
|-----------|----------------------------------------|------|---------------------------------------------|
| WORKHORSE | `grok-4.20-beta-0309-reasoning`        | Reasoning | Deep-focus technical analysis — burns tokens deep. Architecture, debugging, sustained multi-step reasoning. |
| CREATIVE  | `grok-4.20-multi-agent-beta-0309`      | Multi-Agent | Goes wide, not deep. xAI's multi-agent model spawns 4–16 internal sub-agents per call (controlled via `reasoning.effort`). Novel ideas, divergent exploration, creative chaos. |
| SPEED     | `grok-4-1-fast-reasoning`              | Fast Reasoning | Keeps things tight. Rapid answers, summaries, small fixes. Low latency, low cost. |
| CONDUCTOR | `grok-4.20-multi-agent-beta-0309`      | Multi-Agent | Synthesizes all persona outputs into a final verdict. Uses multi-agent internally to weigh competing perspectives. |

**Why this matters:** In deliberation mode, a single council round fires three
parallel API calls across three model tiers, then a fourth for synthesis. The
Workhorse reasons deeply on one thread. The Creative fans out to 4–16 internal
agents exploring in parallel. The Speed runner returns a fast baseline. The
Conductor then synthesizes all of it — itself using multi-agent inference to
weigh the competing outputs. This is model diversity as architecture, not just
prompt engineering.

**Token cost per deliberation round:**
Creative and Conductor use `grok-4.20-multi-agent` ($2/Mtok) and each can spawn
up to 16 internal agents. Workhorse uses `grok-4.20-reasoning` ($2/Mtok) for
single-thread depth. Speed uses `grok-4-1-fast-reasoning` ($0.20/Mtok) for the
baseline. A full deliberation round with high effort can consume significant
tokens — plan accordingly.

### Model overrides

Swap models per persona via environment variables:

```bash
COUNCIL_DELIBERATION_WORKHORSE_MODEL=grok-4-0709 \
COUNCIL_DELIBERATION_CREATIVE_MODEL=grok-4.20-multi-agent-beta-0309 \
COUNCIL_DELIBERATION_SPEED_MODEL=grok-4-1-fast-reasoning \
python3 scripts/conductor.py --mode deliberation "Your prompt here."
```

Multi-agent effort (controls internal agent count for multi-agent models):
- `low` / `medium` → 4 agents
- `high` / `xhigh` → 16 agents

Set via `reasoning.effort` in the API payload (REST). The `agent_count` param
is xAI SDK only — REST ignores it.

## Result Envelope

The conductor's last stdout line is always a JSON envelope:

```json
{
  "status": "ok",
  "mode": "deliberation",
  "routed_to": "workhorse",
  "model": "grok-4.20-beta-0309-reasoning",
  "transcript_path": "examples/transcripts/council_transcript_20260315_0700.md",
  "summary": "First 300 chars...",
  "verdict": "Full synthesized output.",
  "discord_posted": true,
  "main_seat_notified": true,
  "total_latency_s": 42.6,
  "rounds": 4,
  "timestamp": "2026-03-15T07:00:00"
}
```

## Design Principles

- **Stdout is the contract.** The JSON envelope is the authoritative result.
- **Discord is supplemental.** Human visibility only — never parse it for continuation.
- **Synchronous execution.** The calling agent runs the conductor and waits.
- **All context in the prompt.** The council has no filesystem or tool access.
- **Fail cleanly.** Errors produce a structured error envelope, not crashes.
- **Minimal scaffolding.** Personas specialize but self-coordinate — no rigid hierarchy.

## Example Transcripts

The [examples/transcripts/](examples/transcripts/) directory contains 15 real
council sessions demonstrating:

- x86-64 assembly code generation and iterative patch review (Binary Forge)
- Self-upgrading the council's own deliberation pipeline (weighted factors,
  consensus heatmaps, crux registers, epistemic auditing)
- Architectural design and tradeoff analysis

## License

MIT — see [LICENSE](LICENSE).

## Funding

This project accepts funding via [FLOSS/fund](https://floss.fund).
See [funding.json](funding.json).

---

Built by [Hans Axelsson](https://github.com/infektyd) as part of the
local-first AI agent infrastructure stack.
