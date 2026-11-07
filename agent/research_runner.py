from concurrent.futures import ThreadPoolExecutor
import logging
import re
import threading
from datetime import datetime, timezone

from sqlalchemy import func

from agent.email_handler import send_feedback_request
from agent.goals import load_goals
from agent.graph import run_research_cycle
from agent.memory.db import ResearchLog, ResearchRun, get_session

log = logging.getLogger("sisyphus.runner")

_state_lock = threading.Lock()
_active_run_id: int | None = None
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")


class RunInProgress(RuntimeError):
    pass


def queue_research(goal_id: int | None = None) -> int:
    goals = load_goals()
    if goal_id is not None:
        goals = [goal for goal in goals if goal["id"] == goal_id]
        if not goals:
            raise ValueError("Active goal not found")
    else:
        goals = _prioritize_goals_by_research_age(goals)
    if not goals:
        raise ValueError("There are no active goals to research")

    run_id = _reserve_run("manual", goals, goal_id)
    try:
        _executor.submit(_execute_run, run_id, goals)
    except Exception as exc:
        try:
            _finish_run(run_id, "failed", str(exc))
        finally:
            _release_run(run_id)
        raise
    return run_id


def run_scheduled_research() -> None:
    goals = _prioritize_goals_by_research_age(load_goals())
    if not goals:
        log.info("No active goals — nothing to research.")
        return
    try:
        run_id = _reserve_run("scheduled", goals, None)
    except RunInProgress:
        log.info("Skipping scheduled research; another run is already active.")
        return
    _execute_run(run_id, goals)


def recover_stale_runs() -> None:
    with _state_lock:
        query = ResearchRun.status.in_(["queued", "running"])
        if _active_run_id is not None:
            query = query & (ResearchRun.id != _active_run_id)
        with get_session() as session:
            stale_runs = session.query(ResearchRun).filter(query).all()
            for run in stale_runs:
                run.status = "failed"
                run.error = "Process restarted before this run finished."
                run.current_goal_id = None
                run.current_goal_title = ""
                run.finished_at = datetime.now(timezone.utc)
            if stale_runs:
                session.commit()


def _reserve_run(trigger: str, goals: list[dict], goal_id: int | None) -> int:
    global _active_run_id
    with _state_lock:
        if _active_run_id is not None:
            raise RunInProgress("A research run is already active")
        with get_session() as session:
            run = ResearchRun(
                trigger=trigger,
                status="queued",
                goal_id=goal_id,
                goal_title=(
                    goals[0]["title"] if goal_id is not None else "All active goals"
                ),
                total_goals=len(goals),
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            run_id = run.id
        _active_run_id = run_id
        return run_id


def _execute_run(run_id: int, goals: list[dict]) -> None:
    failures: list[str] = []
    try:
        _update_run(
            run_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        for index, goal in enumerate(goals):
            _update_run(
                run_id,
                current_goal_id=goal["id"],
                current_goal_title=goal["title"],
            )
            log.info("Starting research cycle: %r", goal["title"])
            try:
                summary, needs_feedback = run_research_cycle(goal)
                if needs_feedback:
                    match = re.search(r"FEEDBACK_NEEDED:\s*(.+)", summary)
                    question = (
                        match.group(1).strip() if match else "General input needed."
                    )
                    send_feedback_request(
                        goal["title"],
                        f"Goal: {goal['title']}\n\n"
                        f"Context: {goal.get('description', '')}\n\n"
                        f"Question: {question}",
                    )
                    log.info("Feedback request sent for: %r", goal["title"])
            except Exception as exc:
                log.exception("Research cycle failed for %r: %s", goal["title"], exc)
                failures.append(f"{goal['title']}: {exc}")
            _update_run(
                run_id,
                completed_goals=index + 1,
                current_goal_id=None,
                current_goal_title="",
            )

        status = "partial" if failures else "completed"
        _finish_run(run_id, status, "\n".join(failures))
    except Exception as exc:
        log.exception("Research run %s failed: %s", run_id, exc)
        _finish_run(run_id, "failed", str(exc))
    finally:
        _release_run(run_id)


def _update_run(run_id: int, **values) -> None:
    with get_session() as session:
        run = session.query(ResearchRun).filter(ResearchRun.id == run_id).first()
        if run:
            for key, value in values.items():
                setattr(run, key, value)
            session.commit()


def _finish_run(run_id: int, status: str, error: str) -> None:
    _update_run(
        run_id,
        status=status,
        error=error[:4000],
        current_goal_id=None,
        current_goal_title="",
        finished_at=datetime.now(timezone.utc),
    )


def _release_run(run_id: int) -> None:
    global _active_run_id
    with _state_lock:
        if _active_run_id == run_id:
            _active_run_id = None


def _prioritize_goals_by_research_age(goals: list[dict]) -> list[dict]:
    goal_ids = [goal["id"] for goal in goals]
    if not goal_ids:
        return []

    with get_session() as session:
        latest_rows = (
            session.query(ResearchLog.goal_id, func.max(ResearchLog.created_at))
            .filter(ResearchLog.goal_id.in_(goal_ids))
            .group_by(ResearchLog.goal_id)
            .all()
        )
    last_researched = dict(latest_rows)

    def sort_key(goal: dict) -> tuple[int, float, int]:
        latest = last_researched.get(goal["id"])
        if latest is None:
            return (0, 0, -goal["priority"])
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return (1, latest.timestamp(), -goal["priority"])

    return sorted(goals, key=sort_key)
