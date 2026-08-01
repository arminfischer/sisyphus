import re
import json
import logging
from datetime import datetime, timezone

from agent.memory.db import get_session, Goal

log = logging.getLogger("sisyphus.goals")


# ── CRUD ──────────────────────────────────────────────────────────────────────


def load_goals(include_inactive: bool = False) -> list[dict]:
    with get_session() as session:
        q = session.query(Goal)
        if not include_inactive:
            q = q.filter(Goal.active.is_(True))
        goals = q.order_by(Goal.priority.desc()).all()
        return [
            {
                "id": g.id,
                "title": g.title,
                "description": g.description,
                "rationale": g.rationale or "",
                "priority": g.priority,
                "active": g.active,
                "created_at": g.created_at,
                "updated_at": g.updated_at,
            }
            for g in goals
        ]


def get_goal(goal_id: int) -> dict | None:
    with get_session() as session:
        g = session.query(Goal).filter(Goal.id == goal_id).first()
        if not g:
            return None
        return {
            "id": g.id,
            "title": g.title,
            "description": g.description,
            "rationale": g.rationale or "",
            "priority": g.priority,
            "active": g.active,
        }


def save_goal(
    title: str,
    description: str = "",
    rationale: str = "",
    priority: int = 5,
) -> int:
    with get_session() as session:
        goal = Goal(
            title=title,
            description=description,
            rationale=rationale,
            priority=priority,
        )
        session.add(goal)
        session.commit()
        session.refresh(goal)
        gid = goal.id
    log.info(f"Goal created: {title!r} (id={gid})")
    return gid


def update_goal(goal_id: int, **kwargs) -> None:
    with get_session() as session:
        goal = session.query(Goal).filter(Goal.id == goal_id).first()
        if goal:
            for k, v in kwargs.items():
                setattr(goal, k, v)
            goal.updated_at = datetime.now(timezone.utc)
            session.commit()
            log.info(f"Goal {goal_id} updated: {kwargs}")


def deactivate_goal(goal_id: int) -> None:
    update_goal(goal_id, active=False)


def delete_goal(goal_id: int) -> None:
    with get_session() as session:
        goal = session.query(Goal).filter(Goal.id == goal_id).first()
        if goal:
            session.delete(goal)
            session.commit()
            log.info(f"Goal {goal_id} deleted")


# ── LLM-driven goal updates from feedback ─────────────────────────────────────


def update_goals_from_feedback(feedback_text: str, llm_invoke_fn) -> str:
    """
    Parse user feedback with the LLM and apply goal mutations.

    Supports creating NEW goals, updating existing ones, and deactivating
    goals. The LLM is explicitly instructed to create new goals whenever the
    user expresses a new intent, direction, or topic — not only when updating
    existing goals.

    Returns a human-readable summary of changes made.
    """
    goals = load_goals()
    goals_text = (
        "\n".join(
            f"- [id={g['id']}, priority={g['priority']}] {g['title']}\n"
            f"    description: {g['description']}\n"
            f"    rationale: {g['rationale']}"
            for g in goals
        )
        or "(none yet)"
    )

    prompt = f"""You are managing a research agent's goal list.
Based on the user feedback below, decide what changes to make.

CRITICAL: If the user introduces a NEW topic, intent, or direction that is not
already covered by an existing goal, you MUST add it as a new goal. Do not
force-fit new intent into existing goals. Only update an existing goal when the
feedback is clearly about refining or changing that specific goal.

Each goal must include a clear `rationale` explaining WHY the agent should
pursue it and what approach it implies (e.g. "track X so we can decide Y",
"monitor Z because it affects W"). The rationale makes the agent's approach
transparent to the user.

Current goals:
{goals_text}

User feedback:
{feedback_text}

Respond with ONLY a JSON object (no markdown, no prose):
{{
  "add": [
    {{
      "title": "short goal title",
      "description": "what to research / track",
      "rationale": "why this matters and the approach it implies",
      "priority": 5
    }}
  ],
  "update": [
    {{
      "id": 1,
      "title": "...",
      "description": "...",
      "rationale": "...",
      "priority": 5
    }}
  ],
  "deactivate": [1, 2],
  "summary": "One-sentence summary of what changed."
}}
Omit any key that has no changes. When updating, only include fields that
should actually change (but always include `rationale` if you are refining
the goal's purpose)."""

    response = llm_invoke_fn(prompt)
    try:
        # Strip any accidental markdown fences
        cleaned = re.sub(r"```(?:json)?|```", "", response).strip()
        data = json.loads(cleaned)

        for g in data.get("add", []):
            save_goal(
                g["title"],
                g.get("description", ""),
                g.get("rationale", ""),
                g.get("priority", 5),
            )
            log.info(f"Goal added from feedback: {g['title']}")

        for g in data.get("update", []):
            gid = g.pop("id")
            # Only pass known fields
            allowed = {"title", "description", "rationale", "priority", "active"}
            clean = {k: v for k, v in g.items() if k in allowed}
            update_goal(gid, **clean)
            log.info(f"Goal {gid} updated from feedback")

        for gid in data.get("deactivate", []):
            deactivate_goal(gid)
            log.info(f"Goal {gid} deactivated from feedback")

        return data.get("summary", "Goals updated.")
    except Exception as e:
        log.warning(f"Could not parse goal update response: {e}")
        return f"Could not parse goal update: {e}\nRaw response: {response[:300]}"
