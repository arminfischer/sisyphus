import re
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agent.config import settings
from agent.memory.db import get_session, Goal

log = logging.getLogger("sisyphus.goals")


# ── CRUD ──────────────────────────────────────────────────────────────────────


def load_goals() -> list[dict]:
    with get_session() as session:
        goals = (
            session.query(Goal)
            .filter(Goal.active.is_(True))
            .order_by(Goal.priority.desc())
            .all()
        )
        return [
            {
                "id": g.id,
                "title": g.title,
                "description": g.description,
                "priority": g.priority,
            }
            for g in goals
        ]


def save_goal(title: str, description: str = "", priority: int = 5) -> int:
    with get_session() as session:
        goal = Goal(title=title, description=description, priority=priority)
        session.add(goal)
        session.commit()
        session.refresh(goal)
        gid = goal.id
    _sync_goals_file()
    return gid


def update_goal(goal_id: int, **kwargs) -> None:
    with get_session() as session:
        goal = session.query(Goal).filter(Goal.id == goal_id).first()
        if goal:
            for k, v in kwargs.items():
                setattr(goal, k, v)
            goal.updated_at = datetime.now(timezone.utc)
            session.commit()
    _sync_goals_file()


def deactivate_goal(goal_id: int) -> None:
    update_goal(goal_id, active=False)


# ── goals.md sync ─────────────────────────────────────────────────────────────


def _sync_goals_file() -> None:
    goals = load_goals()
    path = Path(settings.goals_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Sisyphus Goals\n\n",
        "*Auto-synced from the database. You may edit this file directly — "
        "changes are picked up on next restart.*\n\n",
    ]
    for g in goals:
        lines.append(f"## [{g['priority']}] {g['title']}\n")
        if g["description"]:
            lines.append(f"{g['description'].strip()}\n")
        lines.append("\n")
    path.write_text("".join(lines), encoding="utf-8")


def load_goals_from_file() -> list[dict]:
    """Parse goals.md → list of dicts. Used on startup to seed an empty DB."""
    path = Path(settings.goals_file)
    if not path.exists():
        return []
    goals: list[dict] = []
    current: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current:
                goals.append(current)
            m = re.match(r"## \[(\d+)\] (.+)", line)
            if m:
                current = {
                    "priority": int(m.group(1)),
                    "title": m.group(2).strip(),
                    "description": "",
                }
            else:
                current = {"priority": 5, "title": line[3:].strip(), "description": ""}
        elif current and line.strip() and not line.startswith("#"):
            current["description"] += line.strip() + " "
    if current:
        goals.append(current)
    return goals


# ── LLM-driven goal updates from feedback ─────────────────────────────────────


def update_goals_from_feedback(feedback_text: str, llm_invoke_fn) -> str:
    """
    Parse user feedback with the LLM and apply goal mutations.
    Returns a human-readable summary of changes made.
    """
    goals = load_goals()
    goals_text = (
        "\n".join(
            f"- [id={g['id']}, priority={g['priority']}] {g['title']}: {g['description']}"
            for g in goals
        )
        or "(none yet)"
    )

    prompt = f"""You are managing a research agent's goal list.
Based on the user feedback below, decide what changes to make.

Current goals:
{goals_text}

User feedback:
{feedback_text}

Respond with ONLY a JSON object (no markdown, no prose):
{{
  "add": [{{"title": "...", "description": "...", "priority": 5}}],
  "update": [{{"id": 1, "title": "...", "description": "...", "priority": 5}}],
  "deactivate": [1, 2],
  "summary": "One-sentence summary of what changed."
}}
Omit any key that has no changes."""

    response = llm_invoke_fn(prompt)
    try:
        # Strip any accidental markdown fences
        cleaned = re.sub(r"```(?:json)?|```", "", response).strip()
        data = json.loads(cleaned)

        for g in data.get("add", []):
            save_goal(g["title"], g.get("description", ""), g.get("priority", 5))
            log.info(f"Goal added: {g['title']}")

        for g in data.get("update", []):
            gid = g.pop("id")
            update_goal(gid, **g)
            log.info(f"Goal {gid} updated")

        for gid in data.get("deactivate", []):
            deactivate_goal(gid)
            log.info(f"Goal {gid} deactivated")

        return data.get("summary", "Goals updated.")
    except Exception as e:
        log.warning(f"Could not parse goal update response: {e}")
        return f"Could not parse goal update: {e}\nRaw response: {response[:300]}"
