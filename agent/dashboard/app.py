from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from agent.goals import (
    deactivate_goal,
    delete_goal,
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
    goals = load_goals()

    with get_session() as session:
        recent_research = (
            session.query(ResearchLog)
            .order_by(ResearchLog.created_at.desc())
            .limit(10)
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
                "goal_id": r.goal_id,
                "summary": r.summary,
                "created_at": r.created_at,
            }
            for r in recent_research
        ]
        email_data = [
            {"subject": e.subject, "created_at": e.created_at} for e in recent_emails
        ]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "goals": goals,
            "research": research_data,
            "emails": email_data,
            "mem_count": collection_count(),
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
                "created_at": str(e.created_at),
            }
            for e in entries
        ]


@app.get("/memory/search")
async def api_memory_search(q: str, n: int = 5):
    return search(q, n_results=n)


@app.get("/health")
async def health():
    return {"status": "ok", "memory_documents": collection_count()}
