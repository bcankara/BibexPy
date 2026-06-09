import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_file() -> str:
    """`.env` dosyasının yolu.

    `BIBEXPY_CONFIG_DIR` env var'ı set ise oradaki `.env` kullanılır (paket
    modu: ~/.bibexpy/.env gibi yazılabilir bir konum). Aksi halde CWD'deki
    `.env` (geliştirme: uvicorn apps/api'den çalışır, CWD = apps/api).
    """
    cfg_dir = os.environ.get("BIBEXPY_CONFIG_DIR")
    if cfg_dir:
        return str(Path(cfg_dir).expanduser() / ".env")
    return ".env"


def _default_storage_dir() -> str:
    """Yazilabilir varsayilan depolama yolu.

    `STORAGE_DIR` env'i (cli.py pakette set eder) varsa pydantic onu okur.
    Aksi halde: gelistirme agacindaki mevcut `./storage` varsa onu (dev'i
    bozmamak icin); yoksa kullanici-yazilabilir `~/.bibexpy/storage`
    (pip/rastgele CWD'de veriyi rastgele yere yazmamak icin).
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

    # LLM yapılandırması — yeni birleşik alanlar
    llm_provider: str = "deepseek"                  # "deepseek" | "openai" | "custom"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"

    # Geriye dönük uyumluluk — eski .env'lerden okuyacak
    deepseek_api_key: str = ""
    deepseek_model: str = ""
    deepseek_base_url: str = ""

    disambiguation_enabled: bool = True
    disambiguation_blocking_threshold: float = 0.75
    disambiguation_auto_approve_threshold: float = 0.95
    # ORCID-öncelikli disambiguation: ambigu gruplar için OpenAlex'e anlık ORCID
    # sorgusu (veride OI yoksa) ve çekileni veriye geri yazma.
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
