import os
from pydantic_settings import BaseSettings, SettingsConfigDict

# When running locally, data lives next to the project root.
# Inside Docker the volume is mounted at /data (overridden via .env).
_DEFAULT_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_embed_model: str = "nomic-embed-text"

    # OmniMCP
    omni_mcp_url: str = "http://192.168.2.117:9090"

    # Email
    gmail_user: str = ""
    gmail_app_password: str = ""
    notify_email: str = "armin.h.g.fischer@proton.me"

    # Dashboard
    dashboard_port: int = 7070

    # Data paths — default to ./data/ locally, overridden to /data in Docker via .env
    data_dir: str = _DEFAULT_DATA
    db_path: str = os.path.join(_DEFAULT_DATA, "sisyphus.db")
    chroma_path: str = os.path.join(_DEFAULT_DATA, "chroma")
    goals_file: str = os.path.join(_DEFAULT_DATA, "goals.md")

    # Timing (canonical defaults — override via .env if needed)
    research_interval_minutes: int = 120  # every 2 hours
    inbox_check_interval_minutes: int = 15  # every 15 min
    daily_report_hour: int = 8  # 08:00 UTC


settings = Settings()
