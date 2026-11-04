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
    """Locate the Next.js static export folder. Returns None if absent — API-only mode.

    In development the frontend usually runs separately (npm run dev, port 3000);
    this function then returns None and the backend serves only the API.
    """
    # 1. Explicit override (env var)
    env = os.environ.get("BIBEXPY_FRONTEND_DIST")
    if env:
        p = Path(env)
        if (p / "index.html").is_file():
            return p

    # 2. apps/web/out/ — if someone ran `npm run build:static`
    here = Path(__file__).resolve().parent
    p = here.parent / "web" / "out"
    if (p / "index.html").is_file():
        return p

    return None


# The SPA HTML must be revalidated on every request. Hashed /_next/static assets
# (served via StaticFiles) are immutable and can be cached for a long time; but
# index.html has a fixed name and points to the new hashed chunks. If it is cached,
# after a wheel upgrade the browser serves the OLD HTML (with old chunk references)
# → old UI. With `no-cache` the browser validates with the ETag every time
# (304 if unchanged — cheap), and is guaranteed to get the new HTML on an upgrade.
_HTML_NO_CACHE = {"Cache-Control": "no-cache"}


def _spa_fallback_response(path: str, root: Path) -> Optional[FileResponse]:
    """Find the right static HTML file for the given URL path.

    If there is a dynamic project ID (`projects/<id>/...`), it tries matching
    against the placeholder `_`. Then it falls back to the root index.html.
    """
    # 1. Exact file
    full = (root / path).resolve()
    # Path traversal guard
    try:
        full.relative_to(root)
    except ValueError:
        return None

    if full.is_file():
        return FileResponse(full, headers=_HTML_NO_CACHE)

    # 2. Directory + index.html
    idx = full / "index.html"
    if idx.is_file():
        return FileResponse(idx, headers=_HTML_NO_CACHE)

    # 3. Dynamic [id] segment: `projects/abc123/upload` → `projects/_/upload/index.html`
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        # parts[1] is the project ID — replace it with `_`
        ph_path = root.joinpath("projects", "_", *parts[2:]) / "index.html"
        if ph_path.is_file():
            return FileResponse(ph_path, headers=_HTML_NO_CACHE)

    # 4. SPA final fallback — root index.html (404.html could also be tried but there is no need)
    fallback = root / "index.html"
    if fallback.is_file():
        return FileResponse(fallback, headers=_HTML_NO_CACHE)
    return None


# The codename is kept in manual sync with cli.py (a single string in the packaged version).
_APP_CODENAME = "Helium"


def _app_version() -> str:
    """Version of the running package — from installed metadata in the packaged
    wheel, 'dev' when running from source. Served via /api/health so the footer
    can show which version is active."""
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

    # CORS — for the frontend making requests from 3000 to 8001 in dev mode.
    # In the packaged binary there is a single port, so CORS is unnecessary but harmless.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        # Local tool: accept localhost / 127.0.0.1 on whatever port.
        # (The explicit list and the regex are evaluated together.) Works even if the port changes.
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routers — under the `/api/` prefix. This avoids URL collisions with
    # frontend routes (e.g. the `/projects` UI page vs the `/api/projects` API
    # endpoint). The same prefix for both dev and the packaged binary.
    for r in (projects, upload, convert, merge, jobs, downloads, filter_router,
              export_router, enrich, disambiguate, prepare, export_folder,
              audit_router, quality_router, records_router, settings_router,
              system_router, tools_router, report_router):
        app.include_router(r.router, prefix="/api")

    # Startup: on first install, create a "Simple Project" from the sample data
    # shipped with the package. Runs once, only in a packaged run (cli.py sets
    # BIBEXPY_SAMPLES_DIR) and only when the store is COMPLETELY empty; thanks to
    # the `.sample_seeded` marker file it is NOT created again if the user deletes the project.
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
                # Existing user store — do not add the sample, but do not try again either.
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

    # Startup: hand the configured OpenAlex API key to bibex_core, whose OpenAlex calls
    # (DOI lookup, enrichment, ORCID/affiliation lookups) read it from a module setting.
    @app.on_event("startup")
    def _apply_openalex_key_on_startup() -> None:
        try:
            from bibex_core.modules import api_utils
            api_utils.set_openalex_api_key(settings.openalex_api_key or None)
        except Exception:
            pass

    # Startup: physically purge soft-deleted analysis folders.
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

    # Meta endpoints — before the frontend mount, under `/api/`
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

    # Frontend mount — if bundled or exported in dev
    frontend_root = _frontend_root()
    if frontend_root is not None:
        # _next/static/* static assets — mounted directly, the fastest path
        next_static = frontend_root / "_next"
        if next_static.is_dir():
            app.mount("/_next", StaticFiles(directory=next_static), name="_next")
        # public/* assets (images, etc.)
        for sub in ("images", "tools", "favicon.ico"):
            sub_path = frontend_root / sub
            if sub_path.is_dir():
                app.mount(f"/{sub}", StaticFiles(directory=sub_path), name=sub)

        # Catch-all GET — SPA fallback (registered AFTER the API and the mounts,
        # it only catches GET requests that match none of the routes above)
        @app.get("/{path:path}", include_in_schema=False)
        async def spa_catchall(path: str, request: Request) -> FileResponse:
            resp = _spa_fallback_response(path, frontend_root)
            if resp is not None:
                return resp
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # Root for the SPA too
        @app.get("/", include_in_schema=False)
        async def spa_root() -> FileResponse:
            return FileResponse(frontend_root / "index.html", headers=_HTML_NO_CACHE)
    else:
        # API-only mode (dev, or the frontend has not been built)
        @app.get("/", tags=["meta"])
        def root() -> dict:
            return {
                "name": "BibexPy v2 API",
                "version": "0.1.0",
                "description": "Self-hosted bibliometric data preparation tool",
                "docs": "/docs",
                "redoc": "/redoc",
                "health": "/health",
                "frontend_hint": "Frontend is not bundled. For development: cd apps/web && npm run dev -> http://localhost:3000",
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
