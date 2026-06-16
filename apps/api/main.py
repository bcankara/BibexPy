"""BibexPy FastAPI application entry point.

Builds the FastAPI app, configures CORS, and mounts all API routers under
the ``/api`` prefix. Optionally serves the static frontend export when one
is present, otherwise runs in API-only mode.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from routers import (
    projects, upload, convert, merge, jobs, downloads,
    filter as filter_router, export as export_router, enrich,
    disambiguate, prepare, export_folder, audit as audit_router,
    quality as quality_router, records as records_router,
    settings as settings_router, system as system_router,
    tools as tools_router, report as report_router,
)


def _frontend_root() -> Optional[Path]:
    """Next.js static export klasörünü bul. Yoksa None — API-only mode.

    Geliştirmede genelde frontend ayrı (npm run dev, port 3000) çalışır;
    bu fonksiyon None döner, backend sadece API serve eder.
    """
    # 1. Açık override (env var)
    env = os.environ.get("BIBEXPY_FRONTEND_DIST")
    if env:
        p = Path(env)
        if (p / "index.html").is_file():
            return p

    # 2. apps/web/out/ — eğer biri `npm run build:static` yaptıysa
    here = Path(__file__).resolve().parent
    p = here.parent / "web" / "out"
    if (p / "index.html").is_file():
        return p

    return None


# SPA HTML her istekte revalidate edilmeli. Hash'li /_next/static asset'leri
# (StaticFiles ile servis edilir) değişmezdir ve uzun süre önbeklenebilir; ama
# index.html sabit isimlidir ve yeni hash'li chunk'lara işaret eder. Önbeklenirse,
# bir wheel yükseltmesinden sonra tarayıcı ESKİ HTML'i (eski chunk referansları)
# servis eder → eski UI. `no-cache` ile tarayıcı her seferinde ETag ile doğrular
# (değişmemişse 304 — ucuz), yükseltmede yeni HTML'i garanti alır.
_HTML_NO_CACHE = {"Cache-Control": "no-cache"}


def _spa_fallback_response(path: str, root: Path) -> Optional[FileResponse]:
    """Verilen URL path'i için doğru statik HTML dosyasını bul.

    Dinamik project ID (`projects/<id>/...`) varsa placeholder `_` ile
    eşleştirmeyi dener. Sonra root index.html'e düşer.
    """
    # 1. Tam dosya
    full = (root / path).resolve()
    # Path traversal guard
    try:
        full.relative_to(root)
    except ValueError:
        return None

    if full.is_file():
        return FileResponse(full, headers=_HTML_NO_CACHE)

    # 2. Dizin + index.html
    idx = full / "index.html"
    if idx.is_file():
        return FileResponse(idx, headers=_HTML_NO_CACHE)

    # 3. Dinamik [id] segment: `projects/abc123/upload` → `projects/_/upload/index.html`
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        # parts[1] proje ID — `_` ile değiştir
        ph_path = root.joinpath("projects", "_", *parts[2:]) / "index.html"
        if ph_path.is_file():
            return FileResponse(ph_path, headers=_HTML_NO_CACHE)

    # 4. SPA son fallback — root index.html (404.html da denenebilir ama gerek yok)
    fallback = root / "index.html"
    if fallback.is_file():
        return FileResponse(fallback, headers=_HTML_NO_CACHE)
    return None


# Codename cli.py ile elle senkron tutulur (paketlenmiş sürümde tek string).
_APP_CODENAME = "Helium"


def _app_version() -> str:
    """Çalışan paketin sürümü — packaged wheel'de installed metadata'dan,
    kaynaktan (dev) çalışırken 'dev'. Footer'da hangi sürümün aktif olduğunu
    göstermek için /api/health ile sunulur."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("bibexpy")
        except PackageNotFoundError:
            return "dev"
    except Exception:
        return "dev"


def create_app() -> FastAPI:
    app = FastAPI(
        title="BibexPy v2 API",
        version="0.1.0",
        description="Self-hosted bibliometric data preparation tool",
    )

    # CORS — dev modunda 3000'den 8001'e istek atan frontend için.
    # Packaged binary'de tek port olduğu için CORS gereksiz ama zarar vermez.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        # Yerel araç: localhost / 127.0.0.1 hangi portta olursa olsun kabul et.
        # (Açık liste + regex birlikte değerlendirilir.) Port değişse bile çalışır.
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API router'ları — `/api/` prefix altında. Bu, frontend route'larıyla
    # (örn. `/projects` UI sayfası vs `/api/projects` API endpoint'i) URL
    # çakışmasını önler. Hem dev hem packaged binary için aynı prefix.
    for r in (projects, upload, convert, merge, jobs, downloads, filter_router,
              export_router, enrich, disambiguate, prepare, export_folder,
              audit_router, quality_router, records_router, settings_router,
              system_router, tools_router, report_router):
        app.include_router(r.router, prefix="/api")

    # Startup: ilk kurulumda paketle gelen örnek veriden "Simple Project" oluştur.
    # Yalnız packaged çalıştırmada (cli.py BIBEXPY_SAMPLES_DIR'i set eder) ve
    # depo TAMAMEN boşken bir kez çalışır; `.sample_seeded` işaret dosyası
    # sayesinde kullanıcı projeyi silerse yeniden OLUŞTURULMAZ.
    @app.on_event("startup")
    def _seed_sample_project_on_startup() -> None:
        try:
            import os
            import shutil

            samples_dir = os.environ.get("BIBEXPY_SAMPLES_DIR", "").strip()
            if not samples_dir:
                return
            src = Path(samples_dir)
            if not src.is_dir():
                return
            marker = settings.storage_path / ".sample_seeded"
            if marker.exists():
                return
            from services import storage as _storage

            if _storage.list_projects():
                # Mevcut kullanıcı deposu — örnek ekleme, ama bir daha da deneme.
                marker.touch()
                return
            meta = _storage.create_project(
                "Simple Project",
                "Built-in sample dataset (Web of Science + Scopus) — try the pipeline end-to-end.",
            )
            raw = _storage.project_dir(meta.id) / "raw"
            copied = 0
            for f in sorted(src.iterdir()):
                if f.is_file() and _storage.detect_file_kind(f.name) != "unknown":
                    shutil.copy2(f, raw / f.name)
                    copied += 1
            marker.touch()
            import logging
            logging.getLogger("uvicorn").info(
                f"Seeded sample project 'Simple Project' ({copied} file(s))"
            )
        except Exception:
            pass

    # Startup: soft-deleted analiz klasörlerini fiziksel temizle.
    @app.on_event("startup")
    def _purge_soft_deleted_on_startup() -> None:
        try:
            from services import storage as _storage, analyses as _analyses
            total = 0
            for proj in _storage.list_projects():
                pid = getattr(proj, "id", None) or (
                    proj.get("id") if hasattr(proj, "get") else None
                )
                if not pid:
                    continue
                try:
                    total += _analyses.purge_soft_deleted(pid)
                except Exception:
                    pass
            if total > 0:
                import logging
                logging.getLogger("uvicorn").info(
                    f"Purged {total} soft-deleted analysis folder(s)"
                )
        except Exception:
            pass

    # Meta endpoint'leri — frontend mount'tan önce, `/api/` altında
    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {
            "status": "ok",
            "version": _app_version(),
            "codename": _APP_CODENAME,
            "storage": str(settings.storage_path),
            "disambiguation_enabled": settings.disambiguation_enabled,
            "frontend_bundled": _frontend_root() is not None,
        }

    # Frontend mount — paketlenmiş veya dev'de export edilmiş ise
    frontend_root = _frontend_root()
    if frontend_root is not None:
        # _next/static/* statik asset'leri — direkt mount, en hızlı yol
        next_static = frontend_root / "_next"
        if next_static.is_dir():
            app.mount("/_next", StaticFiles(directory=next_static), name="_next")
        # public/* asset'leri (görseller, vs.)
        for sub in ("images", "tools", "favicon.ico"):
            sub_path = frontend_root / sub
            if sub_path.is_dir():
                app.mount(f"/{sub}", StaticFiles(directory=sub_path), name=sub)

        # Catch-all GET — SPA fallback (API ve mount'lardan SONRA register edilir,
        # sadece yukarıdaki hiçbir route ile eşleşmeyen GET isteklerini yakalar)
        @app.get("/{path:path}", include_in_schema=False)
        async def spa_catchall(path: str, request: Request) -> FileResponse:
            resp = _spa_fallback_response(path, frontend_root)
            if resp is not None:
                return resp
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # Root da SPA için
        @app.get("/", include_in_schema=False)
        async def spa_root() -> FileResponse:
            return FileResponse(frontend_root / "index.html", headers=_HTML_NO_CACHE)
    else:
        # API-only mode (dev veya frontend build edilmemiş)
        @app.get("/", tags=["meta"])
        def root() -> dict:
            return {
                "name": "BibexPy v2 API",
                "version": "0.1.0",
                "description": "Self-hosted bibliometric data preparation tool",
                "docs": "/docs",
                "redoc": "/redoc",
                "health": "/health",
                "frontend_hint": "Frontend bundled değil. Geliştirme için: cd apps/web && npm run dev → http://localhost:3000",
            }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
