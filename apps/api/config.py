import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_file() -> str:
    """Path of the `.env` file.

    If the `BIBEXPY_CONFIG_DIR` env var is set, the `.env` there is used (package
    mode: a writable location such as ~/.bibexpy/.env). Otherwise the `.env` in
    the CWD (development: uvicorn runs from apps/api, CWD = apps/api); if that
    does not exist either, ~/.bibexpy/.env (aligned with the settings router's
    write path — otherwise keys saved from the UI would be read from a different
    file).
    """
    cfg_dir = os.environ.get("BIBEXPY_CONFIG_DIR")
    if cfg_dir:
        return str(Path(cfg_dir).expanduser() / ".env")
    if Path(".env").exists():
        return ".env"
    home_env = Path.home() / ".bibexpy" / ".env"
    if home_env.exists():
        return str(home_env)
    return ".env"


def _default_storage_dir() -> str:
    """Writable default storage path.

    If the `STORAGE_DIR` env var is set (cli.py sets it in the package), pydantic
    reads it. Otherwise: the existing `./storage` in the development tree, if
    present (so as not to break dev); else the user-writable `~/.bibexpy/storage`
    (so that data is not written to a random place under pip / an arbitrary CWD).
    """
    if Path("./storage").is_dir():
        return "./storage"
    return str(Path.home() / ".bibexpy" / "storage")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_host: str = "127.0.0.1"
    api_port: int = 8001
    api_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"

    storage_dir: str = _default_storage_dir()

    scopus_api_key: str = ""
    semantic_scholar_api_key: str = ""
    unpaywall_email: str = ""
    crossref_email: str = ""
    # OpenAlex API key (free). Without a key, search calls draw on a small daily budget
    # shared by everyone behind the same IP; singleton /works/doi:X lookups are free.
    openalex_api_key: str = ""

    # LLM configuration — new unified fields
    llm_provider: str = "deepseek"                  # "deepseek" | "openai" | "custom"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"

    # Backward compatibility — will read from old .env files
    deepseek_api_key: str = ""
    deepseek_model: str = ""
    deepseek_base_url: str = ""

    disambiguation_enabled: bool = True
    disambiguation_blocking_threshold: float = 0.75
    disambiguation_auto_approve_threshold: float = 0.95
    # ORCID-first disambiguation: for ambiguous groups, an on-the-fly ORCID query
    # to OpenAlex (if the data has no OI) and writing the fetched value back to the data.
    disambiguation_orcid_lookup: bool = True
    disambiguation_orcid_persist: bool = True

    @property
    def effective_llm_api_key(self) -> str:
        return self.llm_api_key or self.deepseek_api_key

    @property
    def effective_llm_base_url(self) -> str:
        return self.llm_base_url or self.deepseek_base_url or "https://api.deepseek.com"

    @property
    def effective_llm_model(self) -> str:
        return self.llm_model or self.deepseek_model or "deepseek-v4-flash"

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


settings = Settings()


def reload_settings() -> None:
    """Re-read `.env` and update the GLOBAL `settings` object IN PLACE.

    Called when the settings are saved from the UI — so that API keys etc. take
    effect immediately without restarting the server. Since other modules hold the
    SAME object via `from config import settings`, the field values are copied
    into the existing object instead of assigning a new one.

    Note: because of pydantic precedence, real environment variables override .env;
    so the STORAGE_DIR that the launcher sets at process start changes only on
    restart (the UI states it that way too).
    """
    fresh = Settings()
    for name in Settings.model_fields:
        setattr(settings, name, getattr(fresh, name))
