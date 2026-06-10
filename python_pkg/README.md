<p align="center">
  <a href="https://bibexpy.com">
    <img src="https://raw.githubusercontent.com/bcankara/BibexPy/main/apps/web/public/images/bibexpy-logo-full.png" alt="BibexPy — V2.0.0 Helium — Bibliometrics Experience with Python" width="420">
  </a>
</p>

**Self-hosted, reproducible bibliometric data preparation for Web of Science & Scopus.**

BibexPy v2.0.0 **"Helium"** merges, filters, harmonizes, enriches and exports WoS + Scopus
records through a local web interface — with full provenance — and keeps your licensed
exports on your own machine. It prepares analysis-ready datasets for VOSviewer, Biblioshiny,
BibTeX, RIS, Excel and more.

## Install

```bash
pip install bibexpy    # macOS / Linux: pip3 install bibexpy
python -m bibexpy      # macOS / Linux: python3 -m bibexpy   (browser opens automatically)
```

> **macOS / Linux:** on most systems the commands are **`python3` / `pip3`** — plain
> `python`/`pip` may not exist (or may point to an old Python 2). If `pip3` itself is
> missing, install it first: `python3 -m ensurepip --upgrade`
> (Debian/Ubuntu: `sudo apt install python3-pip`). On Windows it is usually
> `python` / `pip`.

**`python -m bibexpy` is the recommended way to start the app** — it works on every setup
out of the box, with no PATH configuration. The short `bibexpy` command works too once your
Python `Scripts` folder is on PATH (see the Windows note below).

Requires **Python 3.10+** only — **no Node.js/npm needed** (the interface ships precompiled
inside the package). Works on Windows, macOS and Linux.

```bash
python -m bibexpy --port 8080        # custom port
python -m bibexpy --no-browser       # server only
python -m bibexpy --storage ./data   # custom storage folder
python -m bibexpy --version
```

(The short `bibexpy` command accepts exactly the same options.)

Defaults: UI at `http://127.0.0.1:6060`, data under `~/.bibexpy/storage`, settings/API keys
under `~/.bibexpy/.env` (managed from the in-app Settings page). Press `Ctrl+C` to stop.

### Add `bibexpy` to PATH (Windows)

With **Microsoft Store Python** or `pip install --user`, the `Scripts` folder holding
`bibexpy.exe` is usually **not** on PATH, so PowerShell replies
`bibexpy : The term 'bibexpy' is not recognized…`. Nothing is broken — `python -m bibexpy`
always works. To enable the short command as well:

- **Easiest** — start the app once with `python -m bibexpy`: it detects the situation and
  **offers to add itself to PATH** — answer <kbd>Y</kbd>, open a new terminal, done. (In
  non-interactive shells it prints a personalized copy-paste command instead; you can also
  force it with `python -m bibexpy --add-path`.)
- **Manual** — paste this into PowerShell, then open a **new** terminal:

  ```powershell
  $s = python -c "import sysconfig, os; c=[sysconfig.get_path('scripts','nt_user'), sysconfig.get_path('scripts')]; print(next((p for p in c if 'WindowsApps' not in p and os.path.exists(os.path.join(p,'bibexpy.exe'))), c[0]))"
  [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path","User") + ";$s", "User")
  ```

- **Or use [pipx](https://pipx.pypa.io)** — `pipx install bibexpy` manages PATH for you.

## Highlights (v2)

- **Built-in sample dataset** — first launch creates a ready-to-explore *Simple Project*
  (real WoS + Scopus exports) so you can try the whole pipeline immediately.
- One-click **Smart Merge** — probabilistic record linkage (DOI + Jaro–Winkler), confidence
  scoring, optional borderline review, and a copy-ready methodology paragraph.
- **ORCID-first** author disambiguation + **address harmonization** (organization roll-up,
  country standardization).
- **Multi-source enrichment** (CrossRef, OpenAlex, Scopus, DataCite, Unpaywall, Europe PMC,
  Semantic Scholar) with reverse-DOI recovery — verifiable sources only.
- Reproducible, preset-based **filtering** and a bibliometrically weighted **quality dashboard**.
- Full **provenance**: append-only audit log, snapshots, isolated analyses, auto-generated
  methodology narrative.
- Structured **export**: WoS, VOSviewer TSV, BibTeX, RIS, CSV, TSV, XLSX.

## Links

[Website](https://bibexpy.com) · [Docs](https://bibexpy.com/doc) ·
[GitHub](https://github.com/bcankara/BibexPy) · [Paper (SoftwareX)](https://doi.org/10.1016/j.softx.2025.102098)

## Citation

> Kara, B. C., Şahin, A., & Dirsehan, T. (2025). BibexPy: Harmonizing the bibliometric symphony
> of Scopus and Web of Science. *SoftwareX*, 30, 102098.
> https://doi.org/10.1016/j.softx.2025.102098

## License

GPL-3.0-or-later
