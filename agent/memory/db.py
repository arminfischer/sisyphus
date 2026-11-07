from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from agent.config import settings

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)
_SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, default="")
    rationale = Column(Text, default="")
    active = Column(Boolean, default=True)
    priority = Column(Integer, default=5)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ResearchLog(Base):
    __tablename__ = "research_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(Integer, nullable=True)
    summary = Column(Text, nullable=False)
    sources = Column(Text, default="[]")  # JSON-encoded list of URLs
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class EmailLog(Base):
    __tablename__ = "email_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    direction = Column(String(10), nullable=False)  # "sent" | "received"
    subject = Column(String(512), default="")
    body = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trigger = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="queued")
    goal_id = Column(Integer, nullable=True)
    goal_title = Column(String(256), default="")
    total_goals = Column(Integer, default=0, nullable=False)
    completed_goals = Column(Integer, default=0, nullable=False)
    current_goal_id = Column(Integer, nullable=True)
    current_goal_title = Column(String(256), default="")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(Integer, nullable=True)
    role = Column(String(16), nullable=False)
    mode = Column(String(16), nullable=False, default="ask")
    content = Column(Text, nullable=False)
    sources = Column(Text, default="[]")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_text = Column(Text, nullable=False)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_goal_rationale()
    _migrate_chat_message_goal()


def _migrate_goal_rationale() -> None:
    """Add the `rationale` column to legacy databases if missing."""
    inspector = inspect(engine)
    if "goals" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("goals")}
    if "rationale" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE goals ADD COLUMN rationale TEXT DEFAULT ''"))


def _migrate_chat_message_goal() -> None:
    """Add nullable goal context to existing chat messages without changing history."""
    inspector = inspect(engine)
    if "chat_messages" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("chat_messages")}
    if "goal_id" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN goal_id INTEGER"))


def get_session() -> Session:
    """Return a new SQLAlchemy Session. Use as a context manager: `with get_session() as s:`"""
    return _SessionFactory()
