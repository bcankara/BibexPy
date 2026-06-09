"""Ayarlar endpoint — .env dosyasını UI üzerinden oku/yaz.

Yalnız self-hosted senaryo için tasarlandı. Backend ve frontend aynı makinedeyse
kullanıcı API key ve email gibi alanları UI'dan girebilir.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings


router = APIRouter(prefix="/settings", tags=["settings"])


# LLM sağlayıcı preset'leri — UI dropdown'larda kullanılır
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
        "label": "Özel (OpenAI-uyumlu)",
        "base_url": "",
        "models": [],
        "key_url": "",
    },
}


# Hangi alanlar UI'dan yönetilebilir
EDITABLE_KEYS = {
    # Depolama
    "STORAGE_DIR":              {"label": "Proje Depolama Klasörü",    "secret": False, "group": "storage",
                                 "type": "path",
                                 "default": "./storage",
                                 "hint": "Tüm projeler burada saklanır. Değiştirirseniz eski projeleri yeni klasöre manuel taşıyın. Backend yeniden başlatılmalı."},
    # API anahtarları / e-postaları — zenginleştirme kaynakları.
    # Önemli: Crossref, OpenAlex ve Unpaywall ÜCRETSİZdir; yalnızca Scopus anahtar ister.
    "SCOPUS_API_KEY":           {"label": "Scopus API Key",            "secret": True,  "group": "api",
                                 "hint": "Yalnızca Scopus zenginleştirmesi için gerekli (Elsevier, kurumsal/ücretli). Diğer tüm kaynaklar ücretsizdir."},
    "SEMANTIC_SCHOLAR_API_KEY": {"label": "Semantic Scholar API Key",  "secret": True,  "group": "api",
                                 "hint": "Opsiyonel. Anahtarsız da çalışır; anahtar yalnızca hız limitini yükseltir (ücretsiz)."},
    "UNPAYWALL_EMAIL":          {"label": "Unpaywall Email",           "secret": False, "group": "api",
                                 "hint": "Opsiyonel. Açık erişim PDF bağlantıları için; Unpaywall yalnızca bir e-posta ister (ücretsiz)."},
    "CROSSREF_EMAIL":           {"label": "CrossRef Email (önerilir)", "secret": False, "group": "api",
                                 "hint": "Opsiyonel ama önerilir. CrossRef 'polite pool' ile daha yüksek hız sağlar (ücretsiz)."},
    # LLM (yeni birleşik alanlar)
    "LLM_PROVIDER":             {"label": "LLM Sağlayıcısı",            "secret": False, "group": "llm",
                                 "type": "provider",
                                 "default": "deepseek",
                                 "hint": "Disambiguation modülü için kullanılır. OpenAI-uyumlu herhangi bir API kullanılabilir."},
    "LLM_API_KEY":              {"label": "LLM API Key",                "secret": True,  "group": "llm"},
    "LLM_BASE_URL":             {"label": "LLM Base URL",               "secret": False, "group": "llm",
                                 "default": "https://api.deepseek.com",
                                 "hint": "Sağlayıcı 'Özel' seçilirse buraya tam endpoint URL'i (ör. https://api.together.ai/v1)"},
    "LLM_MODEL":                {"label": "Model",                      "secret": False, "group": "llm",
                                 "type": "model",
                                 "default": "deepseek-v4-flash"},
    # Disambiguation eşikleri
    "DISAMBIGUATION_ENABLED":   {"label": "Disambiguation Aktif",      "secret": False, "group": "llm", "type": "bool"},
    "DISAMBIGUATION_BLOCKING_THRESHOLD":    {"label": "Blocking eşiği",       "secret": False, "group": "llm", "type": "float"},
    "DISAMBIGUATION_AUTO_APPROVE_THRESHOLD":{"label": "Auto-approve eşiği",   "secret": False, "group": "llm", "type": "float"},
}


GROUP_LABELS = {
    "storage": "Depolama",
    "api": "Veri Zenginleştirme API'leri",
    "llm": "LLM (Disambiguation)",
}


def _env_path() -> Path:
    # `BIBEXPY_CONFIG_DIR` set ise (paket modu) oradaki .env'i oku/yaz —
    # yazılabilir kullanıcı konumu (örn. ~/.bibexpy/.env). Yoksa geliştirme
    # modunda config.py'nin yanındaki apps/api/.env.
    import os
    cfg_dir = os.environ.get("BIBEXPY_CONFIG_DIR")
    if cfg_dir:
        d = Path(cfg_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d / ".env"
    here = Path(__file__).resolve().parent.parent  # apps/api (gelistirme)
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
        # Tırnakları temizle
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key] = val
    return out


def _mask_secret(val: str) -> str:
    """API key vb. için sadece son 4 karakteri göster."""
    if not val:
        return ""
    if len(val) <= 6:
        return "•" * len(val)
    return "•" * (len(val) - 4) + val[-4:]


def _write_env(updates: dict[str, str]) -> None:
    """`.env`'i atomik güncelle.

    Mevcut satırların sırasını/yorumlarını koru; sadece KEY=value satırlarını güncelle.
    Yeni anahtarlar dosyanın sonuna eklenir.
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
            # Boşluk veya özel karakter varsa tırnak içine al
            if " " in new_val or '"' in new_val or "$" in new_val:
                new_val_escaped = new_val.replace('"', '\\"')
                out_lines.append(f'{key}="{new_val_escaped}"')
            else:
                out_lines.append(f"{key}={new_val}")
            seen.add(key)
        else:
            out_lines.append(line)

    # Yeni anahtarları sona ekle
    for key, val in updates.items():
        if key in seen:
            continue
        out_lines.append(f"{key}={val}")

    # Atomik yaz (önce tmp, sonra replace)
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


@router.get("", response_model=SettingsResponse)
def get_settings():
    env = _read_env()
    fields: list[SettingField] = []
    for key, meta in EDITABLE_KEYS.items():
        raw = env.get(key, "")
        # LLM alanları için eski DEEPSEEK_* alanlarından fallback (geriye dönük)
        if key == "LLM_API_KEY" and not raw:
            raw = env.get("DEEPSEEK_API_KEY", "")
        elif key == "LLM_BASE_URL" and not raw:
            raw = env.get("DEEPSEEK_BASE_URL", "") or "https://api.deepseek.com"
        elif key == "LLM_MODEL" and not raw:
            old_model = env.get("DEEPSEEK_MODEL", "")
            # Eski "deepseek-chat" → "deepseek-v4-flash" otomatik upgrade önerisi
            if old_model in ("", "deepseek-chat"):
                raw = "deepseek-v4-flash"
            else:
                raw = old_model
        elif key == "LLM_PROVIDER" and not raw:
            raw = "deepseek"
        # STORAGE_DIR için runtime'da resolve edilmiş hali göster
        elif key == "STORAGE_DIR" and not raw:
            raw = str(settings.storage_path)
        is_set = bool(raw and raw != "")
        # Secret'leri maskeli döndür
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
    return SettingsResponse(
        groups=GROUP_LABELS,
        fields=fields,
        env_file=str(_env_path()),
        notes=[
            "Ayarlar .env dosyasına yazılır.",
            "Backend'in yeniden başlatılması bazı değişikliklerin uygulanması için gerekebilir.",
            "API anahtarları maskelenmiş gösterilir; aynı değer kalsın istiyorsanız alanı dokunmayın.",
        ],
        llm_providers=providers,
    )


class ValidatePathPayload(BaseModel):
    path: str
    create_if_missing: bool = False


@router.post("/validate-path")
def validate_path(payload: ValidatePathPayload):
    """Verilen path'i kontrol et — var mı, klasör mü, yazılabilir mi.

    Frontend STORAGE_DIR gibi alanları girerken kullanır.
    """
    raw = payload.path.strip()
    if not raw:
        return {"valid": False, "exists": False, "writable": False, "is_dir": False,
                "absolute": False, "resolved": "", "message": "Path boş"}
    try:
        p = Path(raw).expanduser()
    except Exception as e:
        return {"valid": False, "message": f"Path okunamadı: {e}", "exists": False,
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
                    **info, "message": "Klasör mevcut değil (create_if_missing=true gönderebilirsiniz)"}

    if not p.is_dir():
        return {"valid": False, "exists": True, "writable": False, "is_dir": False,
                **info, "message": "Mevcut ama dizin değil (dosya?)"}

    # Yazılabilirlik testi — küçük bir tmp dosya oluştur/sil
    test = p / ".bibex_writable_check"
    try:
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        writable = True
    except Exception:
        writable = False

    # İçindeki mevcut proje sayısı (meta.json olan alt klasörler)
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
        "message": "Hazır" if writable else "Yazılamıyor — yetki sorunu olabilir",
    }


class UpdatePayload(BaseModel):
    updates: dict[str, Any]


@router.put("", response_model=SettingsResponse)
def update_settings(payload: UpdatePayload):
    # Yalnız EDITABLE_KEYS güncellenebilir
    clean: dict[str, str] = {}
    for key, val in payload.updates.items():
        key = key.strip().upper()
        if key not in EDITABLE_KEYS:
            raise HTTPException(400, f"unknown_key: {key}")
        meta = EDITABLE_KEYS[key]
        if val is None:
            continue
        # Mask karakterlerinden oluşan değeri "değişmedi" olarak yorumla
        if isinstance(val, str) and meta.get("secret"):
            if val.strip() == "" or set(val.strip()) <= {"•"}:
                continue  # değiştirme
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

    _write_env(clean)
    return get_settings()
