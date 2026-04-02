# Claw Operator's Council Toolkit — Installation Guide

## Requirements

- Python 3.10+
- [OpenClaw](https://github.com/openclaw/openclaw) installed and configured
- xAI API key (get one at https://console.x.ai)
- Optional: Discord webhook or OpenClaw Discord channel (for human-visible output)

---

## Step 1: Extract the Toolkit

```bash
mkdir -p ~/.openclaw/workspace/skills/council
cd ~/.openclaw/workspace/skills/council
unzip claw-council-toolkit.zip -d .
```

Or if you prefer manual placement:

```bash
cp -r council ~/.openclaw/workspace/skills/
```

---

## Step 2: Configure Your API Key

Copy the example config and fill in your details:

```bash
cd ~/.openclaw/workspace/skills/council
cp config.env.example config.env
nano config.env   # or vim, code, etc.
```

**Required: Set your xAI API key**

```env
XAI_API_KEY=xai-your-key-here
```

You can also:
- Place your key in `~/.xai-key` (one line, no quotes)
- The script will also check `~/.openclaw/openclaw.json` for a stored key

**Optional: Configure Discord output**

If you want council results posted to Discord, uncomment and fill in your values:

```env
COUNCIL_DISCORD_GUILD_ID=YOUR_GUILD_ID
COUNCIL_DISCORD_CHANNEL_ID=YOUR_CHANNEL_ID
COUNCIL_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## Step 3: Validate the Setup

```bash
cd ~/.openclaw/workspace/skills/council
python3 scripts/validate.py
```

Expected output:
```
✓ Config loaded
✓ API key found
✓ Notify target configured
```

If you see errors, check `config.env` and make sure your xAI key is valid.

---

## Step 4: Run Your First Council

```bash
# Quick verdict (single persona)
python3 scripts/conductor.py "What are the tradeoffs between SQLite and PostgreSQL for a small SaaS?"

# Full deliberation (3 personas + synthesis)
python3 scripts/conductor.py --mode deliberation "Design the auth system for a multi-agent platform."
```

You'll see progress output in your terminal. The final line is always a JSON result envelope.

---

## Step 5: Integrate with OpenClaw (Optional)

Add to your OpenClaw agent's skill registry to invoke via natural language:

```bash
# Add to your agent's skills config or trigger phrases:
"consult the council", "run a council", "council review"
```

---

## Troubleshooting

**"XAI_API_KEY not found"**
- Make sure `config.env` exists and contains a valid key
- Or place your key in `~/.xai-key`

**"openclaw CLI not found"**
- Install OpenClaw CLI: `pip install openclaw`
- Make sure it's in your PATH

**Rate limiting (429 errors)**
- The script retries automatically with backoff
- Reduce `COUNCIL_MAX_RETRIES` in config if you're on a tight budget

**Transcripts not generating**
- Check that `scripts/` is writable
- Log output: `tail -f scripts/logs/council.log`

---

## What's Included

```
council/
├── SKILL.md          ← OpenClaw agent instructions
├── README.md         ← Human-facing overview
├── INSTALL.md        ← You are here
├── config.env.example ← Clean config template
└── scripts/
    ├── bridge.py      ← xAI API wrapper
    ├── conductor.py   ← Main orchestrator
    ├── config.py      ← Config loader
    ├── discord.py     ← Discord posting
    ├── notify.py      ← OpenClaw main-seat notify
    ├── transcript.py  ← Transcript generation
    └── validate.py    ← Pre-flight checks
```

---

Questions? Open an issue or reach out. This is the Hacker's Edition — we expect you to tinker.

*Built with the Claw Operator's Council Toolkit v1.0*
