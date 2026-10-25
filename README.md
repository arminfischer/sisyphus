# Sisyphus

A persistent research agent. Runs on a schedule in Docker, uses OpenRouter's GPT-OSS 20B model, builds a growing memory database, and communicates via email.

---

## Architecture

```
[OpenRouter — openai/gpt-oss-20b]
    ↕ HTTPS API
[Research host — Docker]
  ├── sisyphus agent  (LangGraph research loop)
  ├── ChromaDB        (semantic vector memory, embedded)
  ├── SQLite          (goals, research log, email history)
    └── FastAPI         (objective and research dashboard, port 7070)
        ↕
[Gmail SMTP/IMAP → your inbox]
```

---

## LLM Stack

The agent uses OpenRouter's OpenAI-compatible API with the `openai/gpt-oss-20b` model.

| Role | Model |
|---|---|
| Primary reasoning + tool use | `openai/gpt-oss-20b` |

Set `LLM_BASE_URL=https://openrouter.ai/api/v1` and `LLM_MODEL=openai/gpt-oss-20b` in `.env`. Create an API key at [OpenRouter](https://openrouter.ai/settings/keys) and set `LLM_API_KEY`; keep the key out of source control.

---

## Project Structure

```
sisyphus/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── publish.ps1              # deploy to Synology
├── data/
│   └── (sqlite db + chroma vectors — created at runtime)
└── agent/
    ├── config.py            # all settings (pydantic-settings + .env)
    ├── goals.py             # goal CRUD + LLM-driven updates from feedback
    ├── email_handler.py     # Gmail SMTP send + IMAP receive
    ├── graph.py             # LangGraph agent loop
    ├── main.py              # scheduler (APScheduler) + startup checks
    ├── memory/
    │   ├── db.py            # SQLite schema (goals, research_log, emails)
    │   └── vector.py        # ChromaDB semantic memory
    ├── tools/
    │   ├── web_search.py    # DuckDuckGo (no API key required)
    │   └── pdf_reader.py    # fetch + extract text from PDF URLs
    └── dashboard/
        ├── app.py           # FastAPI dashboard and objective controls
        └── templates/
            └── index.html
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```powershell
Copy-Item .env.example .env
```

Key variables:

| Variable | Description |
| --- | --- |
| `LLM_BASE_URL` | OpenRouter API endpoint, `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | OpenRouter model id, `openai/gpt-oss-20b` |
| `LLM_API_KEY` | Required OpenRouter API key |
| `GMAIL_USER` | Dedicated agent Gmail account |
| `GMAIL_APP_PASSWORD` | Gmail App Password (see below) |
| `NOTIFY_EMAIL` | Where to send reports and feedback requests |
| `DASHBOARD_PORT` | Dashboard port (default: `7070`) |
| `RESEARCH_INTERVAL_MINUTES` | How often scheduled research runs (default: `120`) |
| `INBOX_CHECK_INTERVAL_MINUTES` | How often the inbox is polled (default: `15`) |
| `DAILY_REPORT_HOUR` | UTC hour for the daily report email (default: `8`) |

### Gmail App Password setup

1. Create a dedicated Gmail account for the agent (e.g. `sisyphus.agent@gmail.com`)
2. Google Account → **Security** → **2-Step Verification** (must be enabled) → **App Passwords**
3. Generate a password, paste it into `GMAIL_APP_PASSWORD`

> No paid Proton Mail plan or mail server required.

---

## Local Development

Run the agent locally with a Python virtual environment before deploying.

```powershell
cd c:\Users\Armin\vs\sisyphus

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Run
python -m agent.main
```

Data is stored in `./data/` locally (SQLite, ChromaDB) — no Docker needed.  
Dashboard: [http://localhost:7070](http://localhost:7070)

### Useful one-liners

```powershell
# Dashboard only (with hot-reload)
python -m uvicorn agent.dashboard.app:app --reload --port 7070

# Trigger one research cycle manually
python -c "
from agent.graph import run_research_cycle
summary, needs_fb = run_research_cycle({'id':1,'title':'test','description':'what is LangGraph?'})
print(summary[:500])
"

# Test email sending
python -c "from agent.email_handler import send_test_email; send_test_email()"

# Check inbox for unread feedback
python -c "from agent.email_handler import fetch_unread_feedback; print(fetch_unread_feedback())"

# Wipe local data and start fresh
Remove-Item -Recurse data\
```

---

## Docker Deployment (Synology)

```bash
docker compose build
docker compose up -d
```

Dashboard: `http://nas-ip:7070`  
Logs: `docker logs -f sisyphus`

The `sisyphus_data` Docker volume persists all data (ChromaDB, SQLite) across container restarts and rebuilds.

---

## Publishing to Synology

Use `publish.ps1` to sync code and restart the container on the NAS:

```powershell
.\publish.ps1
```

What it does:

1. `robocopy` — stages changed files to `D:\sisyphus` (skips `.env`, `data/`, `*.db`)
2. `rsync` — syncs the staged files to the Synology over SSH
3. SSH — runs `docker compose down && build --no-cache && up -d`

**One-time SSH prereqs:**

```powershell
# Set up key-based SSH auth (no password prompt during deploy)
ssh-copy-id armin@192.168.2.117
```

`rsync` must be available locally — it ships with Git for Windows (`C:\Program Files\Git\usr\bin\rsync.exe`) or WSL.

The live `data/` directory and `.env` on the NAS are **never overwritten** by the publish script.

---

## How Sisyphus Works

### Scheduler loop

| Job | Frequency | Action |
| --- | --- | --- |
| `check_inbox` | Every 15 min | Polls Gmail IMAP for replies; passes content to LLM → updates goals |
| `run_research` | Every 2 hours by default | Runs active goals, starting with never-researched or oldest-researched objectives |
| `send_report` | Daily at 08:00 UTC | Compiles and emails a summary of recent research |

### Research cycle (LangGraph)

```
agent → (tool calls?) → tools → agent
    → (turn budget reached) → synthesize → critique
    → (insufficient?) → bounded research pass
    → (sufficient or review limit reached) → store → END
```

The agent plans, calls `web_search` / `read_pdf` as needed, then writes findings to ChromaDB (semantic memory) and SQLite (research log).

A **self-critique** step runs before results are stored: an independent LLM call reviews the summary against the goal and its rationale. Each pass has a 12-turn research budget; reaching it triggers a synthesis rather than storing a partial tool-call response. If review finds gaps, the agent gets another bounded pass, up to 2 critique retries. Objectives remain active across scheduled runs, with previous research used as a baseline.

### Memory

- **ChromaDB** — semantic vector store; past research is retrieved by similarity and injected into every new research cycle's system prompt
- **SQLite** — structured log of all goals, research entries, and sent/received emails

### Goal management

Goals are stored in SQLite and managed entirely through the dashboard UI or email.

Each goal has:

- **Title** — short label
- **Description** — what to research or track
- **Rationale** — *why* the agent should pursue this goal and the approach it implies; displayed prominently next to the goal so the agent's direction is transparent
- **Priority** — 1–9 (higher = more important)

To add or change goals:

- **Dashboard UI** — create, edit, pause/resume, or delete goals directly at [http://localhost:7070](http://localhost:7070). Each objective shows its priority, research count, and last worked time.
- **Email the agent** — reply to any of its emails with instructions. The agent parses the intent with the LLM and will **create new goals** for new topics/intents, or update/deactivate existing goals as appropriate.

---

## Dashboard

Web UI at `http://nas-ip:7070`:

- Active and paused objectives, with per-objective research history
- Configured research cadence
- Recent research log (last 10 entries) with source links on detail pages
- Emails sent (last 5)
- Memory document count

API endpoints:

| Endpoint | Description |
| --- | --- |
| `GET /goals` | Active goals as JSON |
| `GET /log?limit=20` | Research log as JSON |
| `GET /memory/search?q=...` | Semantic memory search |
| `GET /health` | Health check + memory count |
