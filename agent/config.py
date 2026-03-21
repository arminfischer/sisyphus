from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b-instruct-q4_K_M"
    ollama_embed_model: str = "nomic-embed-text"

    # OmniMCP
    omni_mcp_url: str = "http://192.168.2.117:9090"

    # Email
    gmail_user: str = ""
    gmail_app_password: str = ""
    notify_email: str = "armin.h.g.fischer@proton.me"

    # Dashboard
    dashboard_port: int = 8080

    # Data paths (inside container volume)
    data_dir: str = "/data"
    db_path: str = "/data/sisyphus.db"
    chroma_path: str = "/data/chroma"
    goals_file: str = "/data/goals.md"

    # Timing
    research_interval_minutes: int = 120
    inbox_check_interval_minutes: int = 15
    daily_report_hour: int = 8


settings = Settings()
