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

# Root of this package inside the wheel (the directory containing cli.py).
_PKG_DIR = Path(__file__).resolve().parent
_WEB_DIR = _PKG_DIR / "_web"
_SERVER_DIR = _PKG_DIR / "_server"

__version__ = "2.3.0"
__codename__ = "Helium"   # v2 release codename (successor to v1 "Hydrogen"; H -> He)


# First-run .env template: shows the user where to enter their keys.
# All keys are optional; it also works without keys (enrichment/LLM are skipped gracefully).
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
    """Write a commented template if `.env` does not exist (leave it alone if it does)."""
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
    """Use the requested port if free; otherwise try the next 20, then ask the OS for a free port."""
    if _port_is_free(host, preferred):
        return preferred
    for p in range(preferred + 1, preferred + 21):
        if _port_is_free(host, p):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _read_env_value(env_file: Path, key: str) -> str | None:
    """Read a single KEY value from the `.env` file (None if absent)."""
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
    """On Windows, if the `bibexpy` command is not on PATH, return the Scripts
    folder that contains the exe (so a copy-paste fix can be shown to the user).

    Typical scenario: Microsoft Store Python or `pip install --user` →
    the Scripts folder is not on PATH → "bibexpy is not recognized". The user
    can still start it with `python -m bibexpy`, so this hint appears at exactly
    that moment.
    """
    if os.name != "nt":
        return None
    import shutil
    import sysconfig

    if shutil.which("bibexpy"):
        return None  # already on PATH — no hint needed
    # ORDER MATTERS: user scheme (nt_user) first — in Store Python / --user
    # installs the exe lands there. sysconfig's default 'scripts' path can point
    # to the virtual Program Files\WindowsApps path in Store Python (it appears
    # to "exist" from inside the process but is NOT ACCESSIBLE from outside)
    # → filter out WindowsApps.
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
    """PERMANENTLY add the Scripts folder to the user PATH (HKCU\\Environment).

    setx is NOT used — it truncates the value at 1024 characters. With winreg the
    entry is appended at the end, preserving the existing value type
    (REG_SZ / REG_EXPAND_SZ); it is idempotent. WM_SETTINGCHANGE is broadcast so
    that newly opened terminals see the updated PATH (terminals that are already
    open are not affected). Returns True on success.
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
                return True  # already added
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
    """Open the browser once the server responds on /api/health.

    Runs in a separate thread; it waits for the server to come up so the browser
    does not show 'connection refused'.
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
        "--no-supervisor",
        action="store_true",
        help="Gözetmen (otomatik yeniden başlatma) olmadan tek süreçte çalıştır (debug)",
    )
    p.add_argument(
        "--add-path",
        action="store_true",
        help='Windows: add the "bibexpy" command to your user PATH (no prompt) and start',
    )
    p.add_argument("--version", action="version", version=f"BibexPy {__version__} ({__codename__})")
    return p


# The file handle faulthandler writes to must stay open for the whole process lifetime.
_CRASH_LOG_FH = None


def _supervise(argv: list[str] | None) -> int:
    """Run the server as a child process; restart it after abnormal exits.

    A native fault (e.g. a hardware-triggered SIMD crash inside numpy) kills
    the Python process instantly with no traceback — without a supervisor the
    user is left staring at a dead prompt. The child sets BIBEXPY_SUPERVISED=1
    and runs the real server; this parent only watches exit codes. Rapid
    crash loops give up after a few strikes instead of spinning forever.
    """
    import subprocess

    base = list(argv) if argv is not None else sys.argv[1:]
    env = dict(os.environ, BIBEXPY_SUPERVISED="1")
    strikes = 0
    first = True
    while True:
        cmd = [sys.executable, "-m", "bibexpy"] + base
        # Do not reopen the browser on restart — the tab is already open.
        if not first and "--no-browser" not in cmd:
            cmd.append("--no-browser")
        started = time.monotonic()
        try:
            code = subprocess.call(cmd, env=env)
        except KeyboardInterrupt:
            return 0
        if code == 0:
            return 0
        lived = time.monotonic() - started
        strikes = strikes + 1 if lived < 60 else 1
        crash_log = Path(os.environ.get("BIBEXPY_CONFIG_DIR") or (Path.home() / ".bibexpy")) / "crash.log"
        if strikes >= 4:
            print(f"\n  ERROR: server crashed {strikes} times in a row (last exit code {code}); giving up.")
            print(f"  Crash details (if captured): {crash_log}")
            return 1
        print(f"\n  WARNING: server exited abnormally (exit code {code}) after {lived:.0f}s.")
        print(f"  This is usually a native crash - details (if captured): {crash_log}")
        print("  Restarting in 2 seconds... (Ctrl+C to stop)\n")
        try:
            time.sleep(2)
        except KeyboardInterrupt:
            return 0
        first = False


def main(argv: list[str] | None = None) -> int:
    # On non-UTF-8 codepages (cp1254/cp850 etc.) the Windows console can crash with
    # UnicodeEncodeError on Turkish characters or arrow symbols.
    # Set stdout/stderr to UTF-8; errors='replace' → never raises.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    args = _build_parser().parse_args(argv)

    # 0. Supervisor: the server runs in a separate child process; a native crash
    # (e.g. a hardware-triggered SIMD fault) must not kill the app permanently, it
    # should restart automatically. The child process skips this block with
    # BIBEXPY_SUPERVISED=1.
    if os.environ.get("BIBEXPY_SUPERVISED") != "1" and not args.no_supervisor:
        return _supervise(argv)

    # 1. Package integrity check — give a meaningful error if installed without running the build script.
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

    # 2. Configuration + storage folders (writable user location).
    config_dir = Path(args.config_dir).expanduser() if args.config_dir else Path.home() / ".bibexpy"
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file = config_dir / ".env"
    _write_env_template(env_file)  # commented template on first run

    # On native crashes (access violation / illegal instruction) record the Python
    # stack — otherwise the process dies WITHOUT A TRACE (like the observed numpy
    # SIMD crash). The file handle must stay open for the process lifetime (global).
    import faulthandler
    global _CRASH_LOG_FH
    try:
        _CRASH_LOG_FH = open(config_dir / "crash.log", "a", encoding="utf-8", errors="replace")
        _CRASH_LOG_FH.write(
            f"\n--- BibexPy v{__version__} session {time.strftime('%Y-%m-%d %H:%M:%S')}"
            f" pid={os.getpid()} ---\n"
        )
        _CRASH_LOG_FH.flush()
        faulthandler.enable(file=_CRASH_LOG_FH, all_threads=True)
    except Exception:
        faulthandler.enable()

    # Storage precedence: --storage > STORAGE_DIR in .env > <config_dir>/storage
    storage = (
        args.storage
        or _read_env_value(env_file, "STORAGE_DIR")
        or str(config_dir / "storage")
    )
    storage_path = Path(storage).expanduser()
    storage_path.mkdir(parents=True, exist_ok=True)

    # 3. Environment variables — main.py + config.py + the settings router read these.
    os.environ["BIBEXPY_CONFIG_DIR"] = str(config_dir)
    os.environ["BIBEXPY_FRONTEND_DIST"] = str(_WEB_DIR)
    os.environ["STORAGE_DIR"] = str(storage_path)

    # Sample data shipped with the package (loaded as "Simple Project" on first
    # install; main.py startup uses it once on an empty store). If the user has
    # already set it (e.g. disabled with an empty string), leave it alone.
    _samples = _PKG_DIR / "_samples" / "simple_project"
    if "BIBEXPY_SAMPLES_DIR" not in os.environ and _samples.is_dir():
        os.environ["BIBEXPY_SAMPLES_DIR"] = str(_samples)

    # 4. For flat imports (from config import ..., from routers import ...)
    #    prepend the embedded server directory to sys.path.
    sys.path.insert(0, str(_SERVER_DIR))

    # 5. Import the application (AFTER the env vars are set — _frontend_root()
    #    reads BIBEXPY_FRONTEND_DIST and mounts the static UI).
    try:
        from main import app  # type: ignore  # noqa: E402  (embedded _server module)
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

    # Windows: if `bibexpy` is not on PATH (Store Python / --user install), OFFER to
    # add it to PATH (pipx ensurepath / rustup pattern). Asked only in an
    # interactive terminal and ONLY ONCE (marker); automation/CI is never blocked.
    # The `--add-path` flag adds it without asking. If not added, a copy-paste hint is shown.
    hint_dir = _windows_path_hint()
    if hint_dir:
        added = False
        prompt_marker = config_dir / ".path_setup_offered"
        # Is it REALLY interactive? BOTH stdin AND stdout must be a TTY.
        # Checking only stdin is not enough: in a process whose output is
        # redirected to a file but whose stdin is attached to a console
        # (service/scheduler/automation), input() would wait forever unanswered
        # and the SERVER WOULD NEVER START (the 2.0.2 bug).
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
