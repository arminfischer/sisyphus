"""
Sisyphus — main entrypoint.

Starts:
  1. FastAPI dashboard (background thread, port DASHBOARD_PORT)
  2. APScheduler with three recurring jobs:
       • check_inbox        — every INBOX_CHECK_INTERVAL_MINUTES
       • run_research       — every RESEARCH_INTERVAL_MINUTES
       • send_daily_report  — daily at DAILY_REPORT_HOUR:00 UTC
"""

import logging
import re
import threading
import time
from datetime import datetime, timezone

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent.config import settings
from agent.email_handler import (
    fetch_unread_feedback,
    send_daily_report,
    send_feedback_request,
)
from agent.goals import (
    load_goals,
    load_goals_from_file,
    save_goal,
    update_goals_from_feedback,
)
from agent.graph import run_research_cycle
from agent.memory.db import ResearchLog, get_session, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("sisyphus")


# ── Scheduled jobs ────────────────────────────────────────────────────────────


def check_inbox() -> None:
    log.info("Checking inbox for feedback…")
    messages = fetch_unread_feedback()
    if not messages:
        return

    from langchain_ollama import ChatOllama

    llm = ChatOllama(
        base_url=settings.ollama_host,
        model=settings.ollama_model,
        temperature=0.1,
    )

    for msg in messages:
        log.info(f"Processing feedback email: {msg['subject']!r}")
        summary = update_goals_from_feedback(
            msg["body"], lambda prompt, _llm=llm: _llm.invoke(prompt).content
        )
        log.info(f"Goal update result: {summary}")


def run_research() -> None:
    goals = load_goals()
    if not goals:
        log.info("No active goals — nothing to research.")
        return

    for goal in goals:
        log.info(f"Starting research cycle: {goal['title']!r}")
        try:
            summary, needs_feedback = run_research_cycle(goal)
            if needs_feedback:
                match = re.search(r"FEEDBACK_NEEDED:\s*(.+)", summary)
                question = match.group(1).strip() if match else "General input needed."
                send_feedback_request(
                    goal["title"],
                    f"Goal: {goal['title']}\n\n"
                    f"Context: {goal.get('description', '')}\n\n"
                    f"Question: {question}",
                )
                log.info(f"Feedback request sent for: {goal['title']!r}")
        except Exception as exc:
            log.exception(f"Research cycle failed for {goal['title']!r}: {exc}")


def _compile_daily_report() -> str:
    with get_session() as session:
        recent = (
            session.query(ResearchLog)
            .order_by(ResearchLog.created_at.desc())
            .limit(20)
            .all()
        )
        entries = [
            {
                "goal_id": r.goal_id,
                "summary": r.summary,
                "created_at": r.created_at,
            }
            for r in recent
        ]

    goals = load_goals()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        f"# Sisyphus Daily Report — {now}",
        "",
        f"## Active Goals ({len(goals)})",
    ]
    for g in goals:
        lines.append(f"- [P{g['priority']}] {g['title']}")

    lines += ["", f"## Recent Research ({len(entries)} entries)"]
    for e in entries:
        ts = e["created_at"].strftime("%H:%M UTC") if e["created_at"] else "?"
        lines.append(f"\n### Goal #{e['goal_id']} — {ts}")
        excerpt = e["summary"][:600]
        if len(e["summary"]) > 600:
            excerpt += "\n[...truncated...]"
        lines.append(excerpt)

    return "\n".join(lines)


def send_report() -> None:
    log.info("Composing and sending daily report…")
    try:
        report = _compile_daily_report()
        send_daily_report(report)
        log.info("Daily report sent.")
    except Exception as exc:
        log.exception(f"Failed to send daily report: {exc}")


# ── Dashboard ─────────────────────────────────────────────────────────────────


def _start_dashboard() -> None:
    try:
        uvicorn.run(
            "agent.dashboard.app:app",
            host="0.0.0.0",
            port=settings.dashboard_port,
            log_level="warning",
            reload=False,
        )
    except Exception as exc:
        log.error(f"Dashboard failed to start on port {settings.dashboard_port}: {exc}")


# ── Startup health checks ─────────────────────────────────────────────────────


def _check_ollama() -> None:
    try:
        from langchain_ollama import ChatOllama

        llm = ChatOllama(
            base_url=settings.ollama_host, model=settings.ollama_model, num_predict=5
        )
        llm.invoke("Say: OK")
        log.info(f"Ollama ✓  ({settings.ollama_host}, model={settings.ollama_model})")
    except Exception as exc:
        log.warning(f"Ollama not reachable on startup: {exc}")


def _check_mcp() -> None:
    from agent.tools.mcp_client import discover_tools

    tools = discover_tools()
    if tools:
        names = [t.get("name", "?") for t in tools]
        log.info(f"OmniMCP ✓  {len(tools)} tools: {names}")
    else:
        log.warning(f"OmniMCP unreachable or no tools at {settings.omni_mcp_url}")


def _seed_goals_from_file() -> None:
    if load_goals():
        return  # DB already has goals
    file_goals = load_goals_from_file()
    for g in file_goals:
        save_goal(g["title"], g.get("description", ""), g.get("priority", 5))
        log.info(f"Seeded goal from goals.md: {g['title']!r}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    log.info("=" * 60)
    log.info("Sisyphus starting up")
    log.info("=" * 60)

    init_db()
    _seed_goals_from_file()
    _check_ollama()
    _check_mcp()

    # Start dashboard in background thread
    dashboard_thread = threading.Thread(target=_start_dashboard, daemon=True)
    dashboard_thread.start()
    log.info(f"Dashboard running on http://0.0.0.0:{settings.dashboard_port}")

    # Build scheduler
    scheduler = BackgroundScheduler(timezone="UTC")

    scheduler.add_job(
        check_inbox,
        IntervalTrigger(minutes=settings.inbox_check_interval_minutes),
        id="inbox",
        next_run_time=datetime.now(timezone.utc),  # run immediately on start
    )
    scheduler.add_job(
        run_research,
        IntervalTrigger(minutes=settings.research_interval_minutes),
        id="research",
    )
    scheduler.add_job(
        send_report,
        CronTrigger(hour=settings.daily_report_hour, minute=0),
        id="daily_report",
    )

    log.info(
        f"Scheduler ready — "
        f"research every {settings.research_interval_minutes}m, "
        f"inbox every {settings.inbox_check_interval_minutes}m, "
        f"daily report at {settings.daily_report_hour:02d}:00 UTC"
    )
    log.info("Press Ctrl+C to stop.")
    try:
        scheduler.start()
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down…")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        pass
