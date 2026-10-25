import os
from pydantic_settings import BaseSettings, SettingsConfigDict

# When running locally, data lives next to the project root.
# Inside Docker the volume is mounted at /data (overridden via .env).
_DEFAULT_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM — OpenRouter's OpenAI-compatible API
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-oss-20b"
    llm_api_key: str = ""

    # Email
    gmail_user: str = ""
    gmail_app_password: str = ""
    notify_email: str = ""

    # Dashboard
    dashboard_port: int = 7070

    # Data paths — default to ./data/ locally, overridden to /data in Docker via .env
    data_dir: str = _DEFAULT_DATA
    db_path: str = os.path.join(_DEFAULT_DATA, "sisyphus.db")
    chroma_path: str = os.path.join(_DEFAULT_DATA, "chroma")

    # Timing (canonical defaults — override via .env if needed)
    research_interval_minutes: int = 120
    inbox_check_interval_minutes: int = 15  # every 15 min
    daily_report_hour: int = 8  # 08:00 UTC


settings = Settings()
