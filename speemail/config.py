from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Microsoft Graph
    azure_client_id: str = ""
    azure_tenant_id: str = "common"
    azure_client_secret: str = ""       # confidential client secret from Azure portal
    azure_redirect_uri: str = "http://localhost:8765/auth/callback"

    # Anthropic
    anthropic_api_key: str = ""

    # Behaviour
    follow_up_days: int = 3
    poll_interval_minutes: int = 15

    # Server
    port: int = 8765
    server_mode: bool = False   # set SERVER_MODE=true in production
    app_password: str = ""      # set APP_PASSWORD in production

    # Paths (derived, not from env)
    @property
    def data_dir(self) -> Path:
        p = Path("data")
        p.mkdir(exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_dir / "speemail.db"

    @property
    def token_cache_path(self) -> Path:
        return self.data_dir / "token_cache.bin"

    @property
    def graph_scopes(self) -> list[str]:
        return [
            "Mail.Read",
            "User.Read",
            "Calendars.Read",
        ]


settings = Settings()
