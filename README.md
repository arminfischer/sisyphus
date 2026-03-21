# Sisyphus

A permanent, autonomous local research agent. Runs 24/7 in Docker on a Synology NAS, uses a locally-hosted LLM, builds a growing memory database over time, and communicates via email.

---

## Architecture

```
[Laptop — Ollama LLM server (16 GB RAM)]
        ↕ local network
[Synology NAS — Docker]
  ├── sisyphus agent  (LangGraph research loop)
  ├── ChromaDB        (semantic vector memory, embedded)
  ├── SQLite          (goals, research log, email history)
  └── FastAPI         (read-only dashboard, port 7070)
        ↕
[OmniMCP at 192.168.2.117:9090]
        ↕
[Gmail SMTP/IMAP → your inbox]
```

---

## LLM Stack

Ollama runs on a separate laptop, exposed on the LAN (`OLLAMA_HOST=0.0.0.0`).

| Role | Model | RAM |
|---|---|---|
| Primary reasoning + tool use | `qwen3:8b` | ~5 GB |
| Embeddings | `nomic-embed-text` | ~300 MB |

> **Why `qwen3:8b`?** It fits comfortably in 16 GB RAM, has strong tool-use and reasoning, and is the best available Qwen3 size for this hardware. `qwen3:30b` would be too slow without a GPU.

**Windows (no bash required):**

1. Download and run the installer from `https://ollama.com/download/windows` — it starts automatically as a tray app.
2. By default Ollama only listens on `localhost`. To expose it on the LAN, the tray app must be killed and `ollama serve` launched directly from a shell that has `OLLAMA_HOST` set (the tray app **ignores** system env vars at runtime):

```powershell
# Kill the tray instance
Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue

# Launch serve with LAN binding in the same shell
$env:OLLAMA_HOST = "0.0.0.0:11434"
Start-Process "ollama" -ArgumentList "serve" -NoNewWindow
```

1. Pull models:

```powershell
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

1. Verify LAN access (replace IP with your laptop's):

```powershell
Invoke-RestMethod http://192.168.2.85:11434/api/tags
```

> To make this permanent: add `OLLAMA_HOST=0.0.0.0:11434` to **System Properties → Environment Variables → System variables**, then sign out and back in so the tray app picks it up on next launch.

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
│   └── goals.md             # seed goals — edit any time
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
    │   ├── pdf_reader.py    # fetch + extract text from PDF URLs
    │   └── mcp_client.py    # OmniMCP HTTP client (auto-discovers tools)
    └── dashboard/
        ├── app.py           # FastAPI read-only dashboard
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
|---|---|
| `OLLAMA_HOST` | LAN IP of the laptop running Ollama, e.g. `http://192.168.2.100:11434` |
| `OLLAMA_MODEL` | Model name, e.g. `qwen2.5:14b-instruct-q4_K_M` |
| `OMNI_MCP_URL` | OmniMCP server, e.g. `http://192.168.2.117:9090` |
| `GMAIL_USER` | Dedicated agent Gmail account |
| `GMAIL_APP_PASSWORD` | Gmail App Password (see below) |
| `NOTIFY_EMAIL` | Where to send reports and feedback requests |
| `DASHBOARD_PORT` | Dashboard port (default: `7070`) |
| `RESEARCH_INTERVAL_MINUTES` | How often a research cycle runs (default: `120`) |
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

Data is stored in `./data/` locally (SQLite, ChromaDB, `goals.md`) — no Docker needed.  
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

The `sisyphus_data` Docker volume persists all data (ChromaDB, SQLite, `goals.md`) across container restarts and rebuilds.

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
|---|---|---|
| `check_inbox` | Every 15 min | Polls Gmail IMAP for replies; passes content to LLM → updates goals |
| `run_research` | Every 2 hours | Runs a LangGraph research cycle for each active goal |
| `send_report` | Daily at 08:00 UTC | Compiles and emails a summary of recent research |

### Research cycle (LangGraph)

```
agent → (tool calls?) → tools → agent
      → (RESEARCH_COMPLETE) → store → END
```

The agent plans, calls `web_search` / `read_pdf` / `call_mcp_tool` as needed, then writes findings to ChromaDB (semantic memory) and SQLite (research log).

### Memory

- **ChromaDB** — semantic vector store; past research is retrieved by similarity and injected into every new research cycle's system prompt
- **SQLite** — structured log of all goals, research entries, and sent/received emails

### Goal management

Goals are stored in both SQLite and `data/goals.md`.

To add or change goals:

- **Email the agent** — reply to any of its emails with instructions; it will parse them with the LLM and update goals automatically
- **Edit `data/goals.md` directly** — changes are seeded into the DB on next restart

Format of `goals.md`:

```markdown
## [8] Monitor local AI developments
Track new model releases and benchmarks for models that run on 16 GB RAM.

## [5] General tech learning
Stay up to date with Python, DevOps, and interesting open-source projects.
```

The number in brackets is the priority (1–10).

---

## Dashboard

Read-only web UI at `http://nas-ip:7070`:

- Active goals with priorities
- Recent research log (last 10 entries)
- Emails sent (last 5)
- Memory document count

API endpoints:

| Endpoint | Description |
|---|---|
| `GET /goals` | Active goals as JSON |
| `GET /log?limit=20` | Research log as JSON |
| `GET /memory/search?q=...` | Semantic memory search |
| `GET /health` | Health check + memory count |
