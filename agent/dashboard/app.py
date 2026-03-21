from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from agent.goals import load_goals
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
