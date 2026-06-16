"""Command-line launcher for BibexPy, the entry point for the ``bibexpy`` command.

Resolves configuration and storage directories, sets the environment variables the
embedded FastAPI server reads, picks a free port, and starts the server with uvicorn.
Bundles the pre-built static frontend (``_web/``) and server (``_server/``) so no
Node.js or build step is required at runtime. Also handles optional browser launch and
Windows PATH setup for the installed command.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# Wheel içindeki bu paketin kökü (cli.py'nin bulunduğu dizin).
_PKG_DIR = Path(__file__).resolve().parent
_WEB_DIR = _PKG_DIR / "_web"
_SERVER_DIR = _PKG_DIR / "_server"

__version__ = "2.0.3"
__codename__ = "Helium"   # v2 surum kod adi (v1 "Hydrogen"in ardili; H -> He)


# Ilk-calistirma .env sablonu — kullanici anahtarlari nereye gireceğini gorur.
# Hepsi opsiyonel; anahtarsiz da calisir (enrichment/LLM graceful skip).
_ENV_TEMPLATE = """\
# BibexPy ayarlari — API anahtarlari (hepsi OPSIYONEL).
# Crossref, OpenAlex ve Unpaywall UCRETSIZdir; yalniz Scopus anahtar ister.

# --- Zenginlestirme kaynaklari (opsiyonel) ---
SCOPUS_API_KEY=
CROSSREF_EMAIL=
UNPAYWALL_EMAIL=
SEMANTIC_SCHOLAR_API_KEY=

# --- LLM (disambiguation icin, opsiyonel) ---
# Saglayici: deepseek | openai | openrouter | custom
LLM_PROVIDER=deepseek
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
"""


def _write_env_template(env_file: Path) -> None:
    """`.env` yoksa yorumlu bir sablon yaz (varsa dokunma)."""
    if env_file.exists():
        return
    try:
        env_file.write_text(_ENV_TEMPLATE, encoding="utf-8")
    except OSError:
        pass


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _resolve_port(host: str, preferred: int) -> int:
    """Istenen port boşsa onu; degilse sonraki 20'yi, sonra OS'tan boş port dener."""
    if _port_is_free(host, preferred):
        return preferred
    for p in range(preferred + 1, preferred + 21):
        if _port_is_free(host, p):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _read_env_value(env_file: Path, key: str) -> str | None:
    """`.env` dosyasından tek bir KEY değerini oku (yoksa None)."""
    if not env_file.is_file():
        return None
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            m = re.match(rf"^{re.escape(key)}\s*=\s*(.*)$", line)
            if m:
                val = m.group(1).strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                return val
    except OSError:
        return None
    return None


def _windows_path_hint() -> str | None:
    """Windows'ta `bibexpy` komutu PATH'te yoksa, exe'nin bulunduğu Scripts
    klasörünü döndür (kullanıcıya kopyala-yapıştır düzeltme göstermek için).

    Tipik senaryo: Microsoft Store Python veya `pip install --user` →
    Scripts klasörü PATH'te değil → "bibexpy is not recognized". Kullanıcı
    `python -m bibexpy` ile başlatabildiği için bu ipucu tam o anda görünür.
    """
    if os.name != "nt":
        return None
    import shutil
    import sysconfig

    if shutil.which("bibexpy"):
        return None  # zaten PATH'te — ipucuna gerek yok
    # SIRA ÖNEMLİ: önce kullanıcı şeması (nt_user) — Store Python / --user
    # kurulumlarında exe oraya düşer. sysconfig'in varsayılan 'scripts' yolu
    # Store Python'da Program Files\WindowsApps sanal yolunu (süreç içinden
    # "var" görünür, dışarıdan ERİŞİLEMEZ) gösterebilir → WindowsApps filtrele.
    candidates: list[Path] = []
    for args in (("scripts", "nt_user"), ("scripts",)):
        try:
            candidates.append(Path(sysconfig.get_path(*args)))
        except Exception:
            pass
    for c in candidates:
        try:
            if "WindowsApps" in str(c):
                continue
            if (c / "bibexpy.exe").is_file():
                return str(c)
        except OSError:
            continue
    return None


def _add_to_user_path_windows(scripts_dir: str) -> bool:
    """Scripts klasörünü kullanıcı PATH'ine KALICI ekle (HKCU\\Environment).

    setx KULLANILMAZ — değeri 1024 karakterde keser. winreg ile mevcut değer
    tipi (REG_SZ / REG_EXPAND_SZ) korunarak sona eklenir; idempotenttir.
    Yeni açılan terminaller güncel PATH'i görsün diye WM_SETTINGCHANGE
    yayınlanır (mevcut açık terminaller etkilenmez). Başarıda True.
    """
    try:
        import ctypes
        import winreg

        target = os.path.normcase(os.path.normpath(scripts_dir))
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                current, vtype = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, vtype = "", winreg.REG_EXPAND_SZ
            parts = [p for p in str(current).split(";") if p.strip()]
            if any(os.path.normcase(os.path.normpath(p)) == target for p in parts):
                return True  # zaten ekli
            new_val = (str(current).rstrip(";") + ";" if current else "") + scripts_dir
            winreg.SetValueEx(key, "Path", 0, vtype, new_val)

        HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 5000, ctypes.byref(ctypes.c_ulong()),
        )
        return True
    except Exception:
        return False


def _open_browser_when_ready(url: str, health_url: str, timeout: float = 30.0) -> None:
    """Sunucu /api/health'e cevap verince tarayıcıyı aç.

    Ayrı thread'de çalışır; sunucunun ayağa kalkmasını bekler ki tarayıcı
    'bağlantı reddedildi' göstermesin.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1.5) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.4)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bibexpy",
        description="Self-hosted bibliometric data preparation tool "
        "(Scopus + Web of Science).",
    )
    p.add_argument("--host", default="127.0.0.1", help="Bağlanılacak host (varsayılan: 127.0.0.1)")
    p.add_argument("--port", type=int, default=6060, help="Port (varsayılan: 6060)")
    p.add_argument(
        "--storage",
        default=None,
        help="Depolama klasörü (varsayılan: ~/.bibexpy/storage veya .env'deki STORAGE_DIR)",
    )
    p.add_argument(
        "--config-dir",
        default=None,
        help="Yapılandırma klasörü (.env burada; varsayılan: ~/.bibexpy)",
    )
    p.add_argument("--no-browser", action="store_true", help="Tarayıcıyı otomatik açma")
    p.add_argument(
        "--add-path",
        action="store_true",
        help='Windows: add the "bibexpy" command to your user PATH (no prompt) and start',
    )
    p.add_argument("--version", action="version", version=f"BibexPy {__version__} ({__codename__})")
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows konsolu (cp1254/cp850 vb.) UTF-8 olmayan codepage'lerde Türkçe
    # karakterlerde veya ok işaretlerinde UnicodeEncodeError ile çökebilir.
    # stdout/stderr'i UTF-8'e ayarla; errors='replace' → asla raise etmez.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    args = _build_parser().parse_args(argv)

    # 1. Paket bütünlüğü kontrolü — build script çalışmadan kurulmuşsa anlamlı hata ver.
    if not (_WEB_DIR / "index.html").is_file():
        sys.stderr.write(
            "ERROR: embedded frontend not found (_web/index.html missing).\n"
            "This wheel was not built properly. Rebuild with scripts/build_wheel.*\n"
        )
        return 2
    if not (_SERVER_DIR / "main.py").is_file():
        sys.stderr.write(
            "ERROR: embedded server not found (_server/main.py missing).\n"
            "This wheel was not built properly. Rebuild with scripts/build_wheel.*\n"
        )
        return 2

    # 2. Yapılandırma + depolama klasörleri (yazılabilir kullanıcı konumu).
    config_dir = Path(args.config_dir).expanduser() if args.config_dir else Path.home() / ".bibexpy"
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file = config_dir / ".env"
    _write_env_template(env_file)  # ilk calistirmada yorumlu sablon

    # Depolama önceliği: --storage > .env'deki STORAGE_DIR > <config_dir>/storage
    storage = (
        args.storage
        or _read_env_value(env_file, "STORAGE_DIR")
        or str(config_dir / "storage")
    )
    storage_path = Path(storage).expanduser()
    storage_path.mkdir(parents=True, exist_ok=True)

    # 3. Ortam değişkenleri — main.py + config.py + settings router bunları okur.
    os.environ["BIBEXPY_CONFIG_DIR"] = str(config_dir)
    os.environ["BIBEXPY_FRONTEND_DIST"] = str(_WEB_DIR)
    os.environ["STORAGE_DIR"] = str(storage_path)

    # Paketle gelen örnek veri (ilk kurulumda "Simple Project" olarak yüklenir;
    # main.py startup'ı boş depoda bir kez kullanır). Kullanıcı önceden set
    # ettiyse (örn. boş string ile kapatma) dokunma.
    _samples = _PKG_DIR / "_samples" / "simple_project"
    if "BIBEXPY_SAMPLES_DIR" not in os.environ and _samples.is_dir():
        os.environ["BIBEXPY_SAMPLES_DIR"] = str(_samples)

    # 4. Flat import'lar (from config import ..., from routers import ...) için
    #    gömülü sunucu dizinini sys.path başına ekle.
    sys.path.insert(0, str(_SERVER_DIR))

    # 5. Uygulamayı import et (env var'lar set edildikten SONRA — _frontend_root()
    #    BIBEXPY_FRONTEND_DIST'i okuyup statik UI'yi mount eder).
    try:
        from main import app  # type: ignore  # noqa: E402  (gömülü _server modülü)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"ERROR: failed to load the server application: {exc}\n")
        return 1

    import uvicorn

    port = _resolve_port(args.host, args.port)
    if port != args.port:
        print(f"  (note: port {args.port} is busy; using {port})")
    url = f"http://{args.host}:{port}/"
    health = f"http://{args.host}:{port}/api/health"

    print(f"  BibexPy v{__version__} \"{__codename__}\"")
    print(f"  -> UI:       {url}")
    print(f"  -> Storage:  {storage_path}")
    print(f"  -> Settings: {env_file}")
    print(f"  (Press Ctrl+C to stop)\n")

    # Windows: `bibexpy` PATH'te değilse (Store Python / --user kurulum) PATH'e
    # eklemeyi TEKLİF ET (pipx ensurepath / rustup deseni). Yalnız etkileşimli
    # terminalde ve BİR KEZ sorulur (marker); otomasyon/CI asla bloklanmaz.
    # `--add-path` bayrağı sormadan ekler. Eklenmezse kopyala-yapıştır ipucu.
    hint_dir = _windows_path_hint()
    if hint_dir:
        added = False
        prompt_marker = config_dir / ".path_setup_offered"
        # GERÇEKTEN etkileşimli mi? stdin VE stdout'un İKİSİ de TTY olmalı.
        # Yalnız stdin'e bakmak yetmez: çıktısı dosyaya yönlendirilmiş ama
        # stdin'i konsola bağlı bir süreçte (servis/zamanlayıcı/otomasyon)
        # input() cevapsız bekler ve SUNUCU HİÇ BAŞLAMAZDI (2.0.2 hatası).
        def _tty(stream) -> bool:
            try:
                return bool(stream and stream.isatty())
            except Exception:
                return False

        interactive = _tty(sys.stdin) and _tty(sys.stdout)
        if args.add_path or (interactive and not prompt_marker.exists()):
            consent = bool(args.add_path)
            if not consent:
                try:
                    ans = input('  Add the "bibexpy" command to your PATH? [Y/n] ')
                    consent = ans.strip().lower() in ("", "y", "yes", "e", "evet")
                except (EOFError, KeyboardInterrupt):
                    consent = False
            try:
                prompt_marker.touch()
            except OSError:
                pass
            if consent:
                added = _add_to_user_path_windows(hint_dir)
                if added:
                    print("  OK - added to PATH. Open a NEW terminal and simply type: bibexpy\n")
                else:
                    print("  Could not update PATH automatically; manual fix below.\n")
        if not added:
            print('  NOTE: the "bibexpy" command is not on your PATH (this run still works).')
            print("  Permanent fix - run this once in PowerShell, then open a NEW terminal:")
            print('    [Environment]::SetEnvironmentVariable("Path", '
                  '[Environment]::GetEnvironmentVariable("Path","User") + '
                  f'";{hint_dir}", "User")')
            print("  Or simply always start it with:  python -m bibexpy\n")

    if not args.no_browser:
        threading.Thread(
            target=_open_browser_when_ready, args=(url, health), daemon=True
        ).start()

    try:
        uvicorn.run(app, host=args.host, port=port, log_level="info")
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
