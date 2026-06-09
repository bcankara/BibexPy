"""API enrichment sarmal — bibex_core'u kullanır.

Tek giriş noktası `run_fill_all`: aktif dataset'i bir kez okur, her DOI için API'yi
TEK SEFER çağırıp dönen tüm boş alanları doldurur (fetch-once-fill-all) ve AKTİF
dataset'e geri yazar. Böylece DOI başına ~7 yerine 1 API çağrısı yapılır ve sonuç
dashboard'ın okuduğu aktif dataset'e yansır. ML geçişi YOKTUR — yalnız API ile doldurma.

Tüm işler uzun sürer — job runner üzerinden çağrılır.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

from config import settings
from jobs.runner import JobContext
from services import analyses, filter_engine, merger, storage
from services.bibex_adapter import _suppress_stdio


def _active_path(project_id: str) -> Path:
    """Aktif analiz dataset'i — filter/quality/disambiguation bunu okur/yazar."""
    src = merger.merged_dataset_path(project_id)
    if src is None:
        raise HTTPException(409, "no_merged_data")
    return src


def _snapshot(project_id: str, df: pd.DataFrame, tag: str) -> str:
    # Snapshot, AKTİF analiz klasörüne yazılır (proje kökü değil) — her merge kendi
    # snapshot'larını taşır, yeni analiz eskileri devralmaz.
    snaps = analyses.work_dir(project_id) / "snapshots"
    snaps.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    p = snaps / f"pre_{tag}_{stamp}.xlsx"
    df.to_excel(p, index=False)
    return str(p.relative_to(storage.settings.storage_path))


def _is_blank(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip() in ("", "nan", "NaN")


# Doluluk oranı raporu için izlenen alanlar (dashboard ile aynı küme)
_RATE_FIELDS = ("DI", "AB", "TI", "AU", "PY", "DE", "ID", "WC", "SC", "C1", "OI", "EM", "CR", "TC", "LA")


def _fill_rate(series) -> float:
    total = len(series)
    if not total:
        return 0.0
    filled = int(sum(0 if _is_blank(x) else 1 for x in series))
    return filled / total


def _mirror_wc_sc(df: pd.DataFrame) -> int:
    """WC<->SC karşılıklı kopya (deterministik): biri doluysa boş olanı onunla doldur.

    DOI/API gerektirmez — WoS kayıtlarındaki tek-eksik WC/SC'yi anında tamamlar.
    Doldurulan hücre sayısını döndürür.
    """
    if "WC" not in df.columns or "SC" not in df.columns:
        return 0
    wc_blank = df["WC"].map(_is_blank)
    sc_blank = df["SC"].map(_is_blank)
    m1 = (~wc_blank) & sc_blank   # WC var, SC yok  -> SC = WC
    m2 = (~sc_blank) & wc_blank   # SC var, WC yok  -> WC = SC
    df.loc[m1, "SC"] = df.loc[m1, "WC"]
    df.loc[m2, "WC"] = df.loc[m2, "SC"]
    return int(m1.sum() + m2.sum())


async def _api_pass(ctx: JobContext, df: pd.DataFrame, *, lo: float = 0.05, hi: float = 0.95) -> dict[str, Any]:
    """df'i YERİNDE güncelle: her DOI için tek extract_metadata çağrısı, tüm boş alanları doldur."""
    from bibex_core.modules.api_utils import extract_metadata

    if "DI" not in df.columns:
        ctx.log("No DI (DOI) column — API pass skipped")
        return {"total": 0, "enriched": 0}

    with_doi = df[df["DI"].astype(str).str.strip().ne("") & df["DI"].astype(str).str.upper().ne("NAN")]
    total = len(with_doi)
    ctx.log(f"API: scanning {total} records with a DOI")

    creds = {
        "scopus_api_key": settings.scopus_api_key or None,
        "semantic_scholar_key": settings.semantic_scholar_api_key or None,
        "unpaywall_email": settings.unpaywall_email or None,
        "crossref_email": settings.crossref_email or None,
    }

    def _one(doi: str, current: dict):
        with _suppress_stdio():
            return extract_metadata(doi, current, **creds)

    enriched = 0
    for i, (idx, row) in enumerate(with_doi.iterrows()):
        if ctx.cancelled:
            ctx.log("Cancelled by user")
            break
        doi = str(row["DI"]).strip()
        current = {c: row[c] for c in df.columns}
        try:
            new_data = await asyncio.to_thread(_one, doi, current)
            updated = 0
            for k, v in new_data.items():
                if k in df.columns and _is_blank(df.at[idx, k]) and not _is_blank(v):
                    df.at[idx, k] = v
                    updated += 1
            if updated:
                enriched += 1
        except Exception as e:
            ctx.log(f"DOI {doi}: {e}")

        if (i + 1) % 10 == 0 or i == total - 1:
            ctx.progress(lo + (hi - lo) * ((i + 1) / max(1, total)))
            ctx.log(f"API: {i + 1}/{total} (enriched: {enriched})")

    return {"total": int(total), "enriched": int(enriched)}


async def _doi_pass(ctx: JobContext, df: pd.DataFrame, *, lo: float = 0.05, hi: float = 0.4) -> dict[str, Any]:
    """Eksik DOI'leri başlık+yıl+yazardan ters arayarak doldur (CrossRef + OpenAlex, doğrulamalı).

    Dengeli eşik + otomatik yazma; emin olunmayan kayıt boş kalır. df YERİNDE güncellenir.
    """
    from bibex_core.modules.api_utils import resolve_doi

    if "DI" not in df.columns or "TI" not in df.columns:
        return {"scanned": 0, "filled": 0}

    blank_di = df[df["DI"].map(_is_blank) & ~df["TI"].map(_is_blank)]
    total = len(blank_di)
    ctx.log(f"DOI lookup: {total} records without a DOI")
    if not total:
        ctx.progress(hi)
        return {"scanned": 0, "filled": 0}

    email = settings.crossref_email or None
    has_au, has_af, has_py = "AU" in df.columns, "AF" in df.columns, "PY" in df.columns
    filled = 0
    for i, (idx, row) in enumerate(blank_di.iterrows()):
        if ctx.cancelled:
            ctx.log("Cancelled by user")
            break
        title = row["TI"]
        authors = row["AU"] if has_au and not _is_blank(row["AU"]) else (row["AF"] if has_af else None)
        year = row["PY"] if has_py else None
        try:
            doi = await asyncio.to_thread(resolve_doi, title, authors, year, crossref_email=email)
        except Exception as e:
            ctx.log(f"DOI lookup error: {e}")
            doi = None
        if doi:
            df.at[idx, "DI"] = doi
            filled += 1
        if (i + 1) % 10 == 0 or i == total - 1:
            ctx.progress(lo + (hi - lo) * ((i + 1) / max(1, total)))
            ctx.log(f"DOI lookup: {i + 1}/{total} (found: {filled})")

    return {"scanned": int(total), "filled": int(filled)}


async def run_fill_all(ctx: JobContext, project_id: str) -> dict[str, Any]:
    """Birleşik 'Fill all missing' — YALNIZ API geçişi (DOI başına tek çağrı, tüm boş alanlar).

    ML geçişi YOKTUR. df bir kez okunur, API ile yerinde doldurulur, aktif dataset'e yazılır.
    İptal edilse bile o ana dek elde edilen API kazanımları korunur.
    """
    src = _active_path(project_id)
    df = await asyncio.to_thread(pd.read_excel, src)
    ctx.log(f"Loaded: {len(df)} records")
    snap = _snapshot(project_id, df, "fill_all")
    ctx.log("Snapshot taken")
    ctx.progress(0.05)

    # Doluluk: işlem ÖNCESİ oranlar (rapor için)
    track = [c for c in _RATE_FIELDS if c in df.columns]
    before = {c: _fill_rate(df[c]) for c in track}

    # 1) WC<->SC karşılıklı kopya — deterministik, DOI gerekmez, API'den ÖNCE (öncelikli).
    mirrored = _mirror_wc_sc(df)
    if mirrored:
        ctx.log(f"WC<->SC mirror: {mirrored} field(s) filled")

    # 2-3) Ters DOI bulma + API geçişi. df YERİNDE güncellenir; iptal (CancelledError
    #    veya ctx.cancelled) durumunda bile o ana dek elde edilen kazanımlar KORUNUR —
    #    geçişleri sarıp finally'de aktif veri kümesine yazıyoruz.
    doi_stats = {"scanned": 0, "filled": 0}
    api_stats = {"total": 0, "enriched": 0}
    addr_stats = {"rows": 0, "addr": 0, "dois": 0}
    cancelled = False
    try:
        doi_stats = await _doi_pass(ctx, df, lo=0.05, hi=0.4)
        api_stats = await _api_pass(ctx, df, lo=0.4, hi=0.9)
        # Adres tamamlama: kurumu olan ama ÜLKESİ eksik adreslere OpenAlex ülkesini
        # deterministik ekle (API, LLM yok, ücretsiz — kullanıcı şeffaf, Fill'in parçası).
        email = settings.crossref_email or settings.unpaywall_email or None
        addr_stats = await _complete_addresses_pass(ctx, df, email, lo=0.9, hi=0.97)
    except asyncio.CancelledError:
        cancelled = True
    finally:
        _mirror_wc_sc(df)  # API tek alanı doldurduysa WC/SC eşitle
        await asyncio.to_thread(df.to_excel, src, index=False)
        filter_engine._DF_CACHE.clear()
        storage.touch_project(project_id)
    if cancelled:
        raise asyncio.CancelledError()

    # Doluluk: işlem SONRASI oranlar + alan-bazlı öncesi/sonrası
    after = {c: _fill_rate(df[c]) for c in track}
    per_field_fill = {c: {"before": round(before[c], 4), "after": round(after[c], 4)} for c in track}
    overall_before = round(sum(before.values()) / len(before), 4) if before else 0.0
    overall_after = round(sum(after.values()) / len(after), 4) if after else 0.0
    ctx.progress(1.0)
    ctx.log(
        f"Done — DOIs found: {doi_stats['filled']}; API: {api_stats['enriched']} records; "
        f"addresses+country: {addr_stats['rows']}; "
        f"fill {round(overall_before*100,1)}% -> {round(overall_after*100,1)}%"
    )

    return {
        "method": "fill_all",
        # Hangi analize ait olduğu — rapor yalnız AKTİF analizde gösterilsin diye
        # (yeni bir merge eski enrichment raporunu taşımamalı).
        "analysis_id": analyses.get_active_analysis_id(project_id),
        "total": int(len(df)),
        "enriched": int(api_stats["enriched"]),
        "api": api_stats,
        "doi": doi_stats,
        "addresses": addr_stats,
        "fill_rate_before": overall_before,
        "fill_rate_after": overall_after,
        "per_field_fill": per_field_fill,
        "snapshot": snap,
    }


async def _complete_addresses_pass(ctx: JobContext, df: pd.DataFrame, email: str | None,
                                   lo: float = 0.95, hi: float = 0.99) -> dict[str, int]:
    """Adres tamamlama PASS'i (YALNIZ API, LLM YOK): kurumu olan ama ÜLKESİ eksik C1
    adreslerine OpenAlex country_code'undan ülkeyi DETERMİNİSTİK ekler.

    df YERİNDE güncellenir (kendi snapshot/yazımı YOK — run_fill_all halleder). Var
    olan ülke EZİLMEZ, kurum/şehir korunur. Ayrım: parse_c1_address.country None ise
    adres ülkesizdir → ekle; doluysa (örn. 'India') atla. Ülke kaynağı: kurum adı
    OpenAlex kurumuyla eşleşirse o ülke, yoksa tek-ülke makalesinde o ülke."""
    from bibex_core.modules.c1_utils import split_c1_addresses, parse_c1_address, append_country_to_c1
    from services.disambiguation.orcid import fetch_affiliations_for_doi
    from services.disambiguation.similarity import normalize_name

    cols = [c for c in ("C1", "C1raw") if c in df.columns]
    if not cols or "DI" not in df.columns:
        return {"rows": 0, "addr": 0, "dois": 0}
    main_col = "C1" if "C1" in df.columns else cols[0]
    rows_changed = 0
    addr_filled = 0
    seen_doi: set[str] = set()
    n = len(df)
    for i, idx in enumerate(df.index):
        if ctx.cancelled:
            break
        doi = df.at[idx, "DI"]
        if doi is None or not str(doi).strip() or str(doi).strip().lower() == "nan":
            continue
        # Ülkesi eksik adres(ler): parse_c1_address.country None olanlar (son bileşen ülke değil).
        tokens: list[str] = []
        for addr in split_c1_addresses(df.at[idx, main_col]):
            if parse_c1_address(addr)["country"] is None:
                parts = [p.strip() for p in addr.split(",") if p.strip()]
                if parts:
                    tokens.append(parts[-1])
        if not tokens:
            continue
        d = str(doi).strip()
        affs = await asyncio.to_thread(fetch_affiliations_for_doi, d, email)
        seen_doi.add(d)
        if not affs:
            continue
        distinct = {c for (_n, c) in affs if c}
        single = next(iter(distinct)) if len(distinct) == 1 else None
        append_map: dict[str, str] = {}
        for tok in tokens:
            ntok = normalize_name(tok)
            matched = None
            if ntok:
                for (iname, ic) in affs:
                    if ic and normalize_name(iname) == ntok:
                        matched = ic
                        break
            country = matched or single
            if country:
                append_map[tok.lower()] = country
        if not append_map:
            continue
        changed = False
        for col in cols:
            old = "" if pd.isna(df.at[idx, col]) else str(df.at[idx, col])
            new = append_country_to_c1(old, append_map)
            if new != old:
                df.at[idx, col] = new
                changed = True
        if changed:
            rows_changed += 1
            addr_filled += len(append_map)
        if (i % 25) == 0:
            ctx.progress(min(hi, lo + (hi - lo) * (i / max(1, n))))
    return {"rows": rows_changed, "addr": addr_filled, "dois": len(seen_doi)}


# ── Geriye uyumluluk: ayrı API endpoint'i (UI artık run_fill_all kullanır; ML yoktur) ──

async def run_api_enrichment(ctx: JobContext, project_id: str, sources: list[str] | None = None) -> dict[str, Any]:
    """Yalnız API geçişi — aktif dataset'e yazar."""
    src = _active_path(project_id)
    df = await asyncio.to_thread(pd.read_excel, src)
    ctx.log(f"Loaded: {len(df)} records")
    snap = _snapshot(project_id, df, "api")
    ctx.progress(0.05)
    stats = await _api_pass(ctx, df, lo=0.05, hi=0.95)
    await asyncio.to_thread(df.to_excel, src, index=False)
    filter_engine._DF_CACHE.clear()
    storage.touch_project(project_id)
    ctx.progress(1.0)
    return {"method": "api", "total": stats["total"], "enriched": stats["enriched"], "snapshot": snap}
