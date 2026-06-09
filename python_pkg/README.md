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
pip install bibexpy
bibexpy            # launches the local web UI (browser opens automatically)
```

Requires **Python 3.10+** only — **no Node.js/npm needed** (the interface ships precompiled
inside the package). Works on Windows, macOS and Linux.

```bash
bibexpy --port 8080          # custom port
bibexpy --no-browser         # server only
bibexpy --storage ./data     # custom storage folder
bibexpy --version
```

Defaults: UI at `http://127.0.0.1:6060`, data under `~/.bibexpy/storage`, settings/API keys
under `~/.bibexpy/.env` (managed from the in-app Settings page). Press `Ctrl+C` to stop.

> **Windows — `bibexpy` is not recognized?** Your Python `Scripts` folder isn't on PATH
> (common with Microsoft Store Python and `pip install --user`). Just run it as
> **`python -m bibexpy`** — on startup it prints your exact `Scripts` path together with a
> copy-paste PowerShell command that fixes PATH permanently. Alternatively install via
> [pipx](https://pipx.pypa.io) (`pipx install bibexpy`), which manages PATH for you.

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
