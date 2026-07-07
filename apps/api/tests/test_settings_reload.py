"""Regression test: saving settings applies them to the running process.

A PUT to /api/settings must both persist to the .env file and refresh the
module-level ``config.settings`` object in place (no server restart), so API
keys entered in the UI take effect immediately.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]


def _fresh_app(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    sys.path.insert(0, str(_API_ROOT))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return app


def test_settings_put_applies_without_restart(monkeypatch, tmp_path):
    app = _fresh_app(monkeypatch, tmp_path)
    import config

    client = TestClient(app)
    assert config.settings.crossref_email == ""

    r = client.put("/api/settings", json={"updates": {"CROSSREF_EMAIL": "reload-test@example.com"}})
    assert r.status_code == 200, r.text

    # 1) .env dosyasına yazıldı
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CROSSREF_EMAIL=reload-test@example.com" in env_text

    # 2) Çalışan sürece ANINDA uygulandı — restart yok
    assert config.settings.crossref_email == "reload-test@example.com"

    # 3) GET yanıtı da yeni değeri gösteriyor
    fields = {f["key"]: f for f in r.json()["fields"]}
    assert fields["CROSSREF_EMAIL"]["value"] == "reload-test@example.com"
    assert fields["CROSSREF_EMAIL"]["is_set"] is True


def test_settings_masked_secret_is_not_overwritten(monkeypatch, tmp_path):
    """Maske karakterleri (••••xxxx) geri gönderilirse mevcut anahtar korunur."""
    app = _fresh_app(monkeypatch, tmp_path)
    import config

    client = TestClient(app)
    r = client.put("/api/settings", json={"updates": {"SCOPUS_API_KEY": "real-key-1234"}})
    assert r.status_code == 200
    assert config.settings.scopus_api_key == "real-key-1234"

    # UI maskeli değeri aynen geri gönderirse değer DEĞİŞMEMELİ
    masked = "•" * 9 + "1234"
    r = client.put("/api/settings", json={"updates": {"SCOPUS_API_KEY": masked}})
    assert r.status_code == 200
    assert config.settings.scopus_api_key == "real-key-1234"
