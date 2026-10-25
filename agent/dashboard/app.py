from pathlib import Path
import json

import markdown
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func

from agent.config import settings
from agent.goals import (
    deactivate_goal,
    delete_goal,
    get_goal,
    load_goals,
    save_goal,
    update_goal,
)
from agent.memory.db import EmailLog, ResearchLog, get_session
from agent.memory.vector import collection_count, search

app = FastAPI(title="Sisyphus", docs_url=None, redoc_url=None)

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    all_goals = load_goals(include_inactive=True)
    goal_titles = {goal["id"]: goal["title"] for goal in all_goals}

    with get_session() as session:
        recent_research = (
            session.query(ResearchLog)
            .order_by(ResearchLog.created_at.desc())
            .limit(10)
            .all()
        )
        goal_progress = (
            session.query(
                ResearchLog.goal_id,
                func.count(ResearchLog.id),
                func.max(ResearchLog.created_at),
            )
            .filter(ResearchLog.goal_id.isnot(None))
            .group_by(ResearchLog.goal_id)
            .all()
        )
        recent_emails = (
            session.query(EmailLog)
            .filter(EmailLog.direction == "sent")
            .order_by(EmailLog.created_at.desc())
            .limit(5)
            .all()
        )
        # Snapshot values while session is still open
        research_data = [
            {
                "id": r.id,
                "goal_id": r.goal_id,
                "goal_title": goal_titles.get(r.goal_id, "Former objective"),
                "summary": r.summary,
                "created_at": r.created_at,
            }
            for r in recent_research
        ]
        email_data = [
            {"subject": e.subject, "created_at": e.created_at} for e in recent_emails
        ]

    progress_by_goal = {
        goal_id: {"research_count": count, "last_researched": last_researched}
        for goal_id, count, last_researched in goal_progress
    }
    for goal in all_goals:
        goal.update(
            progress_by_goal.get(
                goal["id"], {"research_count": 0, "last_researched": None}
            )
        )
    goals = [goal for goal in all_goals if goal["active"]]
    paused_goals = [goal for goal in all_goals if not goal["active"]]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "goals": goals,
            "paused_goals": paused_goals,
            "research": research_data,
            "emails": email_data,
            "mem_count": collection_count(),
            "research_interval_minutes": settings.research_interval_minutes,
        },
    )


# ── Goal CRUD (form-based, dashboard UI) ──────────────────────────────────────


@app.post("/goals/new")
async def create_goal(
    title: str = Form(...),
    description: str = Form(""),
    rationale: str = Form(""),
    priority: int = Form(5),
):
    save_goal(title, description, rationale, priority)
    return RedirectResponse(url="/", status_code=303)


@app.post("/goals/{goal_id}/edit")
async def edit_goal(
    goal_id: int,
    title: str = Form(...),
    description: str = Form(""),
    rationale: str = Form(""),
    priority: int = Form(5),
):
    update_goal(
        goal_id,
        title=title,
        description=description,
        rationale=rationale,
        priority=priority,
    )
    return RedirectResponse(url="/", status_code=303)


@app.post("/goals/{goal_id}/deactivate")
async def deactivate_goal_route(goal_id: int):
    deactivate_goal(goal_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/goals/{goal_id}/activate")
async def activate_goal_route(goal_id: int):
    update_goal(goal_id, active=True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/goals/{goal_id}/delete")
async def delete_goal_route(goal_id: int):
    delete_goal(goal_id)
    return RedirectResponse(url="/", status_code=303)


# ── JSON API ──────────────────────────────────────────────────────────────────


@app.get("/goals")
async def api_goals():
    return load_goals()


@app.get("/log")
async def api_log(limit: int = 20):
    with get_session() as session:
        entries = (
            session.query(ResearchLog)
            .order_by(ResearchLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": e.id,
                "goal_id": e.goal_id,
                "summary": e.summary,
                "sources": json.loads(e.sources or "[]"),
                "created_at": str(e.created_at),
            }
            for e in entries
        ]


@app.get("/research/{entry_id}", response_class=HTMLResponse)
async def research_detail(request: Request, entry_id: int):
    """Full research entry with rendered markdown."""
    with get_session() as session:
        entry = session.query(ResearchLog).filter(ResearchLog.id == entry_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Research entry not found")
        goal = get_goal(entry.goal_id) if entry.goal_id else None
        rendered = markdown.markdown(
            entry.summary or "",
            extensions=["fenced_code", "tables", "toc", "sane_lists"],
        )
        try:
            sources = json.loads(entry.sources or "[]")
        except (TypeError, json.JSONDecodeError):
            sources = []
        data = {
            "id": entry.id,
            "goal_id": entry.goal_id,
            "summary": entry.summary,
            "summary_html": rendered,
            "sources": sources,
            "created_at": entry.created_at,
            "goal": goal,
        }
    return templates.TemplateResponse(
        request=request,
        name="research_detail.html",
        context={"entry": data},
    )


@app.get("/memory/search")
async def api_memory_search(q: str, n: int = 5):
    return search(q, n_results=n)


@app.get("/health")
async def health():
    return {"status": "ok", "memory_documents": collection_count()}
