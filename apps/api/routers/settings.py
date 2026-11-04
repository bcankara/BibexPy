"""Settings endpoints for reading and writing the .env file from the UI.

Exposes a FastAPI router that lists editable configuration fields (storage path,
enrichment API keys, LLM provider/model), validates filesystem paths, and persists
updates back to the .env file. Intended for self-hosted setups where the backend
and frontend run on the same machine.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import reload_settings, settings


router = APIRouter(prefix="/settings", tags=["settings"])


# LLM provider presets — used in the UI dropdowns
LLM_PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash (fast, cheap)"},
            {"id": "deepseek-v4-pro",   "label": "DeepSeek V4 Pro (powerful)"},
        ],
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-5.5",      "label": "GPT-5.5 (frontier)"},
            {"id": "gpt-5.4",      "label": "GPT-5.4 (powerful)"},
            {"id": "gpt-5.4-mini", "label": "GPT-5.4 mini (cheap, fast)"},
            {"id": "gpt-5.4-nano", "label": "GPT-5.4 nano (cheapest)"},
        ],
        "key_url": "https://platform.openai.com/api-keys",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "anthropic/claude-opus-4.8",   "label": "Claude Opus 4.8"},
            {"id": "anthropic/claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
            {"id": "google/gemini-3.5-flash",     "label": "Gemini 3.5 Flash"},
            {"id": "deepseek/deepseek-v4-flash",  "label": "DeepSeek V4 Flash"},
        ],
        "key_url": "https://openrouter.ai/keys",
    },
    "custom": {
        "label": "Custom (OpenAI-compatible)",
        "base_url": "",
        "models": [],
        "key_url": "",
    },
}


# Which fields can be managed from the UI
EDITABLE_KEYS = {
    # Storage
    "STORAGE_DIR":              {"label": "Project Storage Folder",     "secret": False, "group": "storage",
                                 "type": "path",
                                 "default": "./storage",
                                 "hint": "All projects are stored here. Picking a folder activates it immediately - no Save and no restart. Existing projects are not moved automatically; move them manually."},
    # API keys / emails — enrichment sources.
    # Important: Crossref, OpenAlex and Unpaywall are FREE; only Scopus requires a key.
    "SCOPUS_API_KEY":           {"label": "Scopus API Key",            "secret": True,  "group": "api",
                                 "hint": "Needed only for Scopus enrichment (Elsevier, institutional/paid). All other sources are free."},
    "SEMANTIC_SCHOLAR_API_KEY": {"label": "Semantic Scholar API Key",  "secret": True,  "group": "api",
                                 "hint": "Optional. Works without a key; a key only raises the rate limit (free)."},
    "UNPAYWALL_EMAIL":          {"label": "Unpaywall Email",           "secret": False, "group": "api",
                                 "hint": "Optional. Used for open-access PDF links; Unpaywall only asks for an email (free)."},
    "CROSSREF_EMAIL":           {"label": "CrossRef Email (recommended)", "secret": False, "group": "api",
                                 "hint": "Optional but recommended. Gives higher throughput via CrossRef's polite pool (free)."},
    "OPENALEX_API_KEY":         {"label": "OpenAlex API key (optional)", "secret": True, "group": "api",
                                 "hint": "Optional. OpenAlex limits keyless searches to a small free daily budget shared per IP address; a free key gives this installation its own budget."},
    # LLM (new unified fields)
    "LLM_PROVIDER":             {"label": "LLM Provider",                "secret": False, "group": "llm",
                                 "type": "provider",
                                 "default": "deepseek",
                                 "hint": "Used by the disambiguation module. Any OpenAI-compatible API works."},
    "LLM_API_KEY":              {"label": "LLM API Key",                "secret": True,  "group": "llm"},
    "LLM_BASE_URL":             {"label": "LLM Base URL",               "secret": False, "group": "llm",
                                 "default": "https://api.deepseek.com",
                                 "hint": "If the provider is Custom, enter the full endpoint URL here (e.g. https://api.together.ai/v1)"},
    "LLM_MODEL":                {"label": "Model",                      "secret": False, "group": "llm",
                                 "type": "model",
                                 "default": "deepseek-v4-flash"},
    # Disambiguation thresholds
    "DISAMBIGUATION_ENABLED":   {"label": "Disambiguation Enabled",     "secret": False, "group": "llm", "type": "bool"},
    "DISAMBIGUATION_BLOCKING_THRESHOLD":    {"label": "Blocking threshold",    "secret": False, "group": "llm", "type": "float"},
    "DISAMBIGUATION_AUTO_APPROVE_THRESHOLD":{"label": "Auto-approve threshold","secret": False, "group": "llm", "type": "float"},
}


GROUP_LABELS = {
    "storage": "Storage",
    "api": "Data Enrichment APIs",
    "llm": "LLM (Disambiguation)",
}


def _env_path() -> Path:
    # If `BIBEXPY_CONFIG_DIR` is set (package mode), read/write the .env there —
    # a writable user location (e.g. ~/.bibexpy/.env). Otherwise, in development
    # mode, use apps/api/.env next to config.py.
    import os
    cfg_dir = os.environ.get("BIBEXPY_CONFIG_DIR")
    if cfg_dir:
        d = Path(cfg_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d / ".env"
    here = Path(__file__).resolve().parent.parent  # apps/api (development)
    dev_env = here / ".env"
    if not dev_env.exists():
        home_env = Path.home() / ".bibexpy" / ".env"
        if home_env.exists():
            return home_env
    return dev_env


def _read_env() -> dict[str, str]:
    p = _env_path()
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # Strip the quotes
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key] = val
    return out


def _mask_secret(val: str) -> str:
    """Show only the last 4 characters, for API keys etc."""
    if not val:
        return ""
    if len(val) <= 6:
        return "•" * len(val)
    return "•" * (len(val) - 4) + val[-4:]


def _write_env(updates: dict[str, str]) -> None:
    """Update `.env` atomically.

    Keep the order/comments of existing lines; only update the KEY=value lines.
    New keys are appended to the end of the file.
    """
    p = _env_path()
    existing_lines: list[str] = []
    if p.exists():
        existing_lines = p.read_text(encoding="utf-8").splitlines()

    seen: set[str] = set()
    out_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        m = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$', stripped)
        if not m:
            out_lines.append(line)
            continue
        key = m.group(1)
        if key in updates:
            new_val = str(updates[key])
            # Wrap in quotes if it contains a space or special character
            if " " in new_val or '"' in new_val or "$" in new_val:
                new_val_escaped = new_val.replace('"', '\\"')
                out_lines.append(f'{key}="{new_val_escaped}"')
            else:
                out_lines.append(f"{key}={new_val}")
            seen.add(key)
        else:
            out_lines.append(line)

    # Append the new keys at the end
    for key, val in updates.items():
        if key in seen:
            continue
        out_lines.append(f"{key}={val}")

    # Write atomically (tmp first, then replace)
    tmp = p.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(p)


class SettingField(BaseModel):
    key: str
    label: str
    value: str
    is_set: bool
    secret: bool
    group: str
    type: str = "string"  # "string" | "bool" | "float" | "path"
    default: str | None = None
    hint: str | None = None


class LLMProviderPreset(BaseModel):
    id: str
    label: str
    base_url: str
    models: list[dict[str, str]]
    key_url: str = ""


class SettingsResponse(BaseModel):
    groups: dict[str, str]
    fields: list[SettingField]
    env_file: str
    notes: list[str]
    llm_providers: list[LLMProviderPreset] = []
    # Storage transparency: the folder the server is using RIGHT NOW + a warning
    # flag if the configuration in .env has not been applied yet (awaiting restart).
    active_storage: str = ""
    storage_pending_restart: bool = False


@router.get("", response_model=SettingsResponse)
def get_settings():
    env = _read_env()
    fields: list[SettingField] = []
    for key, meta in EDITABLE_KEYS.items():
        raw = env.get(key, "")
        # For the LLM fields, fall back to the old DEEPSEEK_* fields (backward compatibility)
        if key == "LLM_API_KEY" and not raw:
            raw = env.get("DEEPSEEK_API_KEY", "")
        elif key == "LLM_BASE_URL" and not raw:
            raw = env.get("DEEPSEEK_BASE_URL", "") or "https://api.deepseek.com"
        elif key == "LLM_MODEL" and not raw:
            old_model = env.get("DEEPSEEK_MODEL", "")
            # Suggested automatic upgrade of the old "deepseek-chat" → "deepseek-v4-flash"
            if old_model in ("", "deepseek-chat"):
                raw = "deepseek-v4-flash"
            else:
                raw = old_model
        elif key == "LLM_PROVIDER" and not raw:
            raw = "deepseek"
        # For STORAGE_DIR, show the form resolved at runtime
        elif key == "STORAGE_DIR" and not raw:
            raw = str(settings.storage_path)
        is_set = bool(raw and raw != "")
        # Return secrets masked
        display = _mask_secret(raw) if (meta.get("secret") and is_set) else raw
        fields.append(SettingField(
            key=key,
            label=meta["label"],
            value=display,
            is_set=is_set,
            secret=bool(meta.get("secret", False)),
            group=meta["group"],
            type=meta.get("type", "string"),
            default=meta.get("default"),
            hint=meta.get("hint"),
        ))
    providers = [
        LLMProviderPreset(id=pid, **pdata) for pid, pdata in LLM_PROVIDERS.items()
    ]
    # Active storage vs. the one configured in .env: if they differ, a restart is
    # pending (the launcher sets STORAGE_DIR as an environment variable at process
    # start; the new value in .env is only read on restart).
    active_storage = str(settings.storage_path)
    configured = env.get("STORAGE_DIR", "").strip()
    pending_restart = False
    if configured:
        try:
            pending_restart = Path(configured).expanduser().resolve() != settings.storage_path
        except OSError:
            pending_restart = configured != active_storage
    return SettingsResponse(
        active_storage=active_storage,
        storage_pending_restart=pending_restart,
        groups=GROUP_LABELS,
        fields=fields,
        env_file=str(_env_path()),
        notes=[
            "Settings are written to the .env file and applied immediately on save (including the storage folder).",
            "When the storage folder changes, existing projects are not moved automatically - move them manually.",
            "API keys are shown masked; leave a field untouched to keep its value.",
        ],
        llm_providers=providers,
    )


class ValidatePathPayload(BaseModel):
    path: str
    create_if_missing: bool = False


@router.post("/validate-path")
def validate_path(payload: ValidatePathPayload):
    """Check the given path — does it exist, is it a folder, is it writable.

    The frontend uses it when entering fields such as STORAGE_DIR.
    """
    raw = payload.path.strip()
    if not raw:
        return {"valid": False, "exists": False, "writable": False, "is_dir": False,
                "absolute": False, "resolved": "", "message": "Path is empty"}
    try:
        p = Path(raw).expanduser()
    except Exception as e:
        return {"valid": False, "message": f"Path could not be read: {e}", "exists": False,
                "writable": False, "is_dir": False, "absolute": False, "resolved": raw}

    info: dict[str, Any] = {
        "absolute": p.is_absolute(),
        "resolved": str(p.resolve() if p.exists() else p),
    }
    if not p.exists():
        if payload.create_if_missing:
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return {"valid": False, "exists": False, "writable": False, "is_dir": False,
                        **info, "message": f"folder_create_failed: {e}"}
        else:
            return {"valid": False, "exists": False, "writable": False, "is_dir": False,
                    **info, "message": "Folder does not exist (you can send create_if_missing=true)"}

    if not p.is_dir():
        return {"valid": False, "exists": True, "writable": False, "is_dir": False,
                **info, "message": "Exists but is not a directory (a file?)"}

    # Writability test — create/delete a small tmp file
    test = p / ".bibex_writable_check"
    try:
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        writable = True
    except Exception:
        writable = False

    # Number of existing projects inside (subfolders containing meta.json)
    project_count = 0
    try:
        for child in p.iterdir():
            if child.is_dir() and (child / "meta.json").exists():
                project_count += 1
    except Exception:
        pass

    return {
        "valid": writable,
        "exists": True,
        "writable": writable,
        "is_dir": True,
        **info,
        "project_count": project_count,
        "message": "Ready" if writable else "Not writable - possible permission issue",
    }


class UpdatePayload(BaseModel):
    updates: dict[str, Any]


@router.put("", response_model=SettingsResponse)
def update_settings(payload: UpdatePayload):
    # Only EDITABLE_KEYS can be updated
    clean: dict[str, str] = {}
    for key, val in payload.updates.items():
        key = key.strip().upper()
        if key not in EDITABLE_KEYS:
            raise HTTPException(400, f"unknown_key: {key}")
        meta = EDITABLE_KEYS[key]
        if val is None:
            continue
        # Interpret a masked value as "unchanged". _mask_secret leaves the last 4
        # characters visible ("•••••1234") — a check for all-bullets only would miss
        # this and OVERWRITE the real key with the mask string. A real key cannot
        # contain "•"; any value containing a bullet is a mask round-trip → skip.
        if isinstance(val, str) and meta.get("secret"):
            if val.strip() == "" or "•" in val:
                continue  # do not change
        # Bool → "true/false"
        if meta.get("type") == "bool":
            clean[key] = "true" if bool(val) else "false"
        elif meta.get("type") == "float":
            try:
                clean[key] = str(float(val))
            except Exception:
                raise HTTPException(400, f"field_must_be_numeric: {key}")
        else:
            clean[key] = str(val).strip()

    # Storage folder LIVE switch: NO restart needed. The launcher pins
    # STORAGE_DIR as an env var at process start, and in pydantic an env var
    # overrides .env — so writing only to .env was not enough ("I picked it but
    # it doesn't change"). Once the env var is updated here, reload reads the new
    # value; since all path accesses go through settings.storage_path, the switch
    # takes effect immediately. A non-writable folder is rejected with 400 (no
    # half-applied switch).
    if "STORAGE_DIR" in clean and clean["STORAGE_DIR"].strip():
        import os
        new_dir = Path(clean["STORAGE_DIR"].strip()).expanduser()
        try:
            new_dir.mkdir(parents=True, exist_ok=True)
            probe = new_dir / ".bibex_writable_check"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as e:
            raise HTTPException(400, f"storage_dir_not_writable: {e}")
        os.environ["STORAGE_DIR"] = str(new_dir)

    _write_env(clean)
    # Refresh the running process's settings immediately — so API keys etc. take
    # effect without a restart. (Previously it only wrote to .env; since the
    # `settings` object read at module load time stayed stale, keys appeared as
    # "saved but not working".)
    reload_settings()
    # bibex_core keeps the OpenAlex key in a module setting; refresh it as well.
    try:
        from bibex_core.modules import api_utils
        api_utils.set_openalex_api_key(settings.openalex_api_key or None)
    except Exception:
        pass

    if "STORAGE_DIR" in clean:
        # So the dataset cache from the old storage does not get mixed with the new one
        from services import filter_engine
        filter_engine._DF_CACHE.clear()

    return get_settings()
