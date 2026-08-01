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


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_text = Column(Text, nullable=False)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_goal_rationale()


def _migrate_goal_rationale() -> None:
    """Add the `rationale` column to legacy databases if missing."""
    inspector = inspect(engine)
    if "goals" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("goals")}
    if "rationale" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE goals ADD COLUMN rationale TEXT DEFAULT ''"))


def get_session() -> Session:
    """Return a new SQLAlchemy Session. Use as a context manager: `with get_session() as s:`"""
    return _SessionFactory()
