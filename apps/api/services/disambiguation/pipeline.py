"""Disambiguation pipeline orchestration — blocking -> LLM -> sonuç."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

from config import settings
from jobs.runner import JobContext, run_cpu
from services import analyses, merger, storage
from .blocking import (
    build_author_blocks, build_affiliation_blocks, build_author_splits,
    build_country_blocks, build_org_rollup,
)
from .cache import DisambiguationCache
from . import orcid as orcid_mod
from .deepseek_client import (
    DeepSeekClient,
    AUTHOR_SYSTEM_PROMPT,
    AUTHOR_SPLIT_SYSTEM_PROMPT,
    AFFILIATION_SYSTEM_PROMPT,
    COUNTRY_SYSTEM_PROMPT,
    ORG_ROLLUP_SYSTEM_PROMPT,
)


def _project_dir(project_id: str) -> Path:
    """Disambiguation çalışma klasörü = AKTİF ANALİZ klasörü (proje kökü değil).

    Böylece öneriler (disambiguation_*.json), snapshot'lar ve audit'ler ait oldukları
    analizin içinde tutulur; yeni bir merge (yeni analiz) eski önerileri TAŞIMAZ —
    otomatik temiz başlangıç. Hiç analiz yoksa proje köküne düşer (geriye uyumlu).
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return analyses.work_dir(project_id)


def _load_merged(project_id: str) -> pd.DataFrame:
    p = merger.merged_dataset_path(project_id)
    if p is None:
        raise HTTPException(409, "no_merged_data")
    return pd.read_excel(p)


def _distinct_variants(cluster: dict) -> set[str]:
    """Bir kümenin birleştirdiği farklı yazım/varyant kümesi.

    Üye objesi `name_variants` (yazar) veya `variants` (affiliation/org/ülke)
    taşır; hiçbiri yoksa üyenin kendi `id`'si tek varyant sayılır. <2 varyant =
    birleştirilecek bir şey yok (tek-üyeli LLM kümesi) → öneri olarak gösterilmez.
    """
    out: set[str] = set()
    for m in cluster.get("members", []):
        vs = m.get("name_variants") or m.get("variants") or ([m["id"]] if m.get("id") else [])
        out.update(v for v in vs if v)
    return out


def _label_for_llm(members: list[dict], variant_key: str,
                   context_keys: tuple = ()) -> tuple[list[dict], dict]:
    """Her VARYANTI tek tek v1..vN ile etiketleyip LLM'e gonder.

    Neden varyant duzeyi (aday duzeyi degil): aday duzeyinde etiketlemede,
    cok-varyantli tek bir aday ('v1') icindeki varyantlari LLM 'v1_10' gibi
    ALT-INDEKSLIYOR; bu etiketler aday id'siyle eslesmiyor ve kart 'v1_10'
    gibi gorunuyordu. Her varyanta kendi 'vN' etiketini verince LLM'in
    alt-indekslemesine gerek kalmaz; donen yanit dogrudan haritalanir.

    Her oge: {id:'vN', <name|text>: varyant, **baglam}. Donen:
    (LLM ogeleri, {etiket: ham varyant dizesi}).
    """
    text_field = "name" if variant_key == "name_variants" else "text"
    items: list[dict] = []
    by_label: dict[str, str] = {}
    n = 0
    for m in members:
        vs = m.get(variant_key) or ([m["id"]] if m.get("id") else [])
        ctx = {k: m[k] for k in context_keys if m.get(k)}
        for v in vs:
            if not v:
                continue
            n += 1
            label = f"v{n}"
            items.append({"id": label, text_field: v, **ctx})
            by_label[label] = v
    return items, by_label


def _members_from_labels(cluster: dict, by_label: dict, variant_key: str) -> list[dict]:
    """LLM yanitindaki member_ids (v-etiketleri) GERCEK varyant dizelerine cevir;
    her varyant icin tek bir uye olustur. Bilinmeyen etiket -> ham dize (son care)."""
    out: list[dict] = []
    seen: set[str] = set()
    for mid in (cluster.get("member_ids") or []):
        v = by_label.get(str(mid).strip(), str(mid))
        if not v or v in seen:
            continue
        seen.add(v)
        out.append({"id": v, variant_key: [v]})
    return out


def _member_variants(m: dict, key: str) -> list:
    """Bir üyenin varyant listesi; boşsa üyenin kendi 'id'sine düş (LLM üyeleri
    varyantı çoğu zaman 'id' alanında taşır)."""
    vs = m.get(key) or ([m["id"]] if m.get("id") else [])
    return [v for v in vs if v]


def _apply_c1_map(df: pd.DataFrame, cols: list[str], fn) -> int:
    """C1/C1raw kolonlarına fn uygula ve GERÇEKTEN değişen satır sayısını döndür.

    Eskiden affected=len(mapping) idi: eşleşme hiç tutmasa bile sıfır-olmayan bir
    sayı raporluyordu (kullanıcıya '109 değişti' der ama veri değişmezdi). Artık
    yalnız fiilen değişen satırlar sayılır (herhangi bir kolonda)."""
    changed = pd.Series(False, index=df.index)
    for col in cols:
        before = df[col].astype(str)
        after = before.apply(fn)
        df[col] = after
        changed = changed | (after != before)
    return int(changed.sum())


def _client() -> DeepSeekClient:
    api_key = settings.effective_llm_api_key
    if not api_key:
        raise HTTPException(400, "llm_key_missing")
    return DeepSeekClient(
        api_key=api_key,
        base_url=settings.effective_llm_base_url,
        model=settings.effective_llm_model,
    )


def _snapshot(project_dir: Path, df: pd.DataFrame, kind: str) -> Path:
    snaps = project_dir / "snapshots"
    snaps.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    p = snaps / f"pre_{kind}_{stamp}.xlsx"
    df.to_excel(p, index=False)
    return p


# ── ORCID çözümü SENKRON ağ I/O yapar (orcids_for_candidate → fetch_orcids_for_doi →
# requests). Bu yüzden ASLA event loop üzerinde await'siz çağrılmamalı: aksi halde scan
# sırasında loop bloklanır ve cancel/navigasyon/export DONAR (test bulgusu #7'nin ayrı
# kök nedeni). Aşağıdaki yardımcılar asyncio.to_thread ile thread'e alınır; should_cancel
# ile iptalde erken çıkarlar. ──
def _resolve_member_orcid_sets(df, members, email, allow_api, should_cancel=None) -> list[set]:
    """Üye başına ORCID seti (df-first + opsiyonel anlık API). Thread'de çalıştırılmalı."""
    out: list[set] = []
    for m in members:
        if should_cancel and should_cancel():
            break
        out.append(orcid_mod.orcids_for_candidate(df, m, email=email, allow_api=allow_api))
    return out


def _resolve_split_group_sets(df, groups, name, email, allow_api, should_cancel=None) -> list[set]:
    """Bir split önerisinin grup-başına ORCID setleri. Thread'de çalıştırılmalı."""
    out: list[set] = []
    for g in groups:
        if should_cancel and should_cancel():
            break
        cand = {"name_variants": [name], "records": g.get("records", [])}
        out.append(orcid_mod.orcids_for_candidate(df, cand, email=email, allow_api=allow_api))
    return out


async def _compute_author_splits(ctx: JobContext, project_dir: Path, df) -> list[dict]:
    """Yazar AYRIŞTIRMA önerileri. Tier 1 (net, alan-ayrık) deterministik;
    Tier 2 (sınırda, alan-yok kayıt var) LLM hakemliğine — LLM 'keep' derse öneri düşer."""
    splits = await run_cpu(build_author_splits, df, should_cancel=lambda: ctx.cancelled)
    if not splits:
        ctx.log("Splitting: no names to split.")
        return []

    # ── ORCID override: aynı isim, alan-ayrık gruplar gerçekten farklı kişiler mi? ──
    # Grup başına ORCID çöz (df-first, eksikse anlık API). ORCID altın standarttır:
    #   • Tek ortak ORCID ≥2 gruba yayılıyorsa → TEK interdisipliner kişi → ASLA bölme.
    #   • Gruplar farklı ORCID'lerdeyse → kesin farklı kişiler → güçlü split (LLM'siz).
    email = settings.crossref_email or None
    allow_api = settings.disambiguation_orcid_lookup
    orcid_keep = 0
    orcid_split = 0
    for s in splits:
        if ctx.cancelled:
            break
        # SENKRON ağ I/O → thread'e al (event loop'u bloklama; cancel/nav/export donmasın).
        group_sets = await asyncio.to_thread(
            _resolve_split_group_sets, df, s.get("groups", []), s["name"], email, allow_api,
            lambda: ctx.cancelled,
        )
        known = [gs for gs in group_sets if gs]
        if len(known) >= 2:
            # Tüm bilinen gruplar ortak bir ORCID paylaşıyor mu?
            if set.intersection(*known):
                s["decision"] = "keep"        # aynı kişi → bölme
                s["source"] = "orcid"
                s["reason"] = "Same ORCID across groups — one interdisciplinary person"
                orcid_keep += 1
            else:
                s["decision"] = "split"       # farklı ORCID'ler → kesin böl
                s["source"] = "orcid"
                s["tier"] = 1
                s["confidence"] = 1.0
                s["reason"] = "Different ORCID per group — distinct people"
                orcid_split += 1
    if orcid_keep or orcid_split:
        ctx.log(f"ORCID split: {orcid_split} confirmed, {orcid_keep} kept (same person)")

    # ORCID kararı verilmemiş (source != 'orcid') öneriler eski akışa düşer
    undecided = [s for s in splits if s.get("source") != "orcid"]
    tier1 = [s for s in undecided if s.get("tier") == 1]
    tier2 = [s for s in undecided if s.get("tier") == 2]
    for s in tier1:
        s["source"] = "deterministic"
        s["decision"] = "split"
    ctx.log(f"Splitting — Tier 1 (auto): {len(tier1)}, Tier 2 (borderline): {len(tier2)}")

    if tier2:
        client = None
        try:
            client = _client()
        except HTTPException:
            client = None  # API anahtarı yoksa manuel incelemeye bırak
        if client is not None:
            cache = DisambiguationCache(project_dir)
            try:
                for s in tier2:
                    if ctx.cancelled:
                        break
                    payload = {"task": "author_split", "name": s["name"],
                               "groups": [{"fields": g["fields"]} for g in s["groups"]]}
                    ck = cache.hash_key(payload)
                    resp = cache.get(ck)
                    if resp is None:
                        try:
                            resp = await client.chat_json_async(AUTHOR_SPLIT_SYSTEM_PROMPT, payload)
                            cache.set(ck, resp)
                        except Exception as e:
                            ctx.log(f"Split LLM error ({s['name']}): {e}")
                            resp = {}
                    s["source"] = "llm"
                    s["decision"] = resp.get("decision", "review")  # split | keep | review
                    s["confidence"] = resp.get("confidence")
                    if resp.get("reason"):
                        s["reason"] = f"{s['reason']} | AI: {resp['reason']}"
            finally:
                cache.close()
        else:
            for s in tier2:
                s["source"] = "manual"
                s["decision"] = "review"

    # 'keep' (LLM ya da ORCID: tek disiplinlerarası kişi) önerileri listeden çıkar.
    # ORCID ile karar verilenler (split) de dahil — tüm splits üzerinden filtrele.
    return [s for s in splits if s.get("decision") != "keep"]


async def run_author_disambiguation(
    ctx: JobContext, project_id: str, mode: str = "auto",
    max_records: int | None = None,
) -> dict[str, Any]:
    project_dir = _project_dir(project_id)
    df = await asyncio.to_thread(_load_merged, project_id)
    ctx.log(f"Loaded: {len(df)} records")
    if max_records and max_records > 0 and len(df) > max_records:
        df = df.head(max_records)
        ctx.log(f"Test/cost mode: limited to first {max_records} records")
    ctx.log(f"Mode: {mode}")
    ctx.progress(0.05)

    # 1) Blocking
    ctx.log("Blocking + similarity analysis...")
    blocks = await run_cpu(
        build_author_blocks, df,
        settings.disambiguation_blocking_threshold, mode,
        settings.disambiguation_auto_approve_threshold,
        should_cancel=lambda: ctx.cancelled,
    )
    ctx.log(f"{len(blocks)} candidate merge blocks found")
    ctx.progress(0.15)

    all_clusters: list[dict] = []
    all_uncertain: list[dict] = []

    # 1.5) Tier 1 — deterministik auto-merge önerileri (LLM'siz, confidence=1.0)
    auto_count = 0
    for block in blocks:
        for ac in block.get("auto", []):
            variants = ac.get("name_variants", [])
            all_clusters.append({
                "cluster_id": ac["id"],
                "block_key": block["key"],
                "members": [ac],
                "canonical": variants[0] if variants else None,
                "confidence": 1.0,
                "source": "deterministic",
                "tier": 1,
                "reason": "High similarity + shared context (automatic)",
            })
            auto_count += 1
    if auto_count:
        ctx.log(f"Tier 1 (automatic, no LLM): {auto_count} proposals")

    # ORCID write-back istatistiği (Part 2d)
    orcid_stats = {"rows_filled": 0, "dois_fetched": 0}
    email = settings.crossref_email or None
    allow_api = settings.disambiguation_orcid_lookup

    # 2) Tier 2 — belirsiz bloklar. ÖNCE ORCID kararı (df-first, eksikse anlık API),
    #    sonra gerekiyorsa LLM. ORCID altın standart: aynı=merge, farklı=ayrı.
    llm_blocks = [b for b in blocks if b.get("members")]
    ctx.log(f"Tier 2 (review): {len(llm_blocks)} blocks")
    orcid_merges = 0
    orcid_conflicts = 0
    if llm_blocks:
        client = None
        cache = DisambiguationCache(project_dir)
        try:
            for i, block in enumerate(llm_blocks):
                if ctx.cancelled:
                    ctx.log("Cancelled")
                    break

                members = block["members"]
                # 2.1) ORCID ön-pass — üye başına ORCID setini çöz (df-first + on-demand).
                # SENKRON ağ I/O → thread'e al (event loop'u bloklama; aksi halde scan
                # sırasında cancel/navigasyon/export DONAR — #7 ek kök neden).
                orcid_sets = await asyncio.to_thread(
                    _resolve_member_orcid_sets, df, members, email, allow_api,
                    lambda: ctx.cancelled,
                )
                verdict = orcid_mod.classify_by_orcid(orcid_sets)

                if verdict == "merge":
                    # Tüm ORCID-bilinen üyeler aynı kişi → Tier 1 auto-merge (LLM'siz)
                    shared = sorted(set.intersection(*[s for s in orcid_sets if s]))
                    variants = sorted({v for m in members for v in (m.get("name_variants") or [])})
                    if len(set(variants)) >= 2:
                        all_clusters.append({
                            "cluster_id": f"{block['key']}__orcid",
                            "block_key": block["key"],
                            "members": members,
                            "canonical": variants[0] if variants else None,
                            "confidence": 1.0,
                            "source": "orcid",
                            "tier": 1,
                            "reason": f"Same ORCID ({', '.join(shared)})",
                        })
                        orcid_merges += 1
                    ctx.progress(0.15 + 0.8 * ((i + 1) / len(llm_blocks)))
                    continue  # LLM'e gitme

                if verdict == "conflict":
                    # Ayrık ORCID'ler → farklı kişiler → birleştirme YOK (LLM'e gitme)
                    orcid_conflicts += 1
                    block_names = sorted({v for m in members for v in (m.get("name_variants") or [])})
                    all_uncertain.append({
                        "block_key": block["key"],
                        "names": block_names,
                        "reason": "Different ORCIDs — distinct people (not merged)",
                    })
                    ctx.progress(0.15 + 0.8 * ((i + 1) / len(llm_blocks)))
                    continue

                # 2.2) ORCID belirsiz → LLM hakemliği (mevcut akış). LLM yapılandırılmamışsa
                #      bloğu manuel incelemeye bırak (job çökmesin).
                if client is None:
                    try:
                        client = _client()
                    except HTTPException:
                        client = False  # anahtar yok — bu çalıştırmada LLM kullanılmaz
                if not client:
                    block_names = sorted({v for m in members for v in (m.get("name_variants") or [])})
                    all_uncertain.append({
                        "block_key": block["key"], "names": block_names,
                        "reason": "No ORCID and no LLM configured — manual review",
                    })
                    ctx.progress(0.15 + 0.8 * ((i + 1) / len(llm_blocks)))
                    continue
                items, by_label = _label_for_llm(members, "name_variants", ("affiliations", "coauthors", "year_range"))
                payload = {"task": "author_disambiguation", "candidates": items}
                cache_key = cache.hash_key(payload)
                cached = cache.get(cache_key)
                if cached:
                    resp = cached
                    ctx.log(f"[{i+1}/{len(llm_blocks)}] cache hit ({block['key']})")
                else:
                    try:
                        resp = await client.chat_json_async(AUTHOR_SYSTEM_PROMPT, payload)
                        cache.set(cache_key, resp)
                        ctx.log(f"[{i+1}/{len(llm_blocks)}] LLM ({block['key']}): {len(resp.get('clusters', []))} cluster")
                    except Exception as e:
                        ctx.log(f"[{i+1}/{len(llm_blocks)}] ERROR: {e}")
                        continue

                for c in resp.get("clusters", []):
                    c["block_key"] = block["key"]
                    c["cluster_id"] = f"{block['key']}__{c.get('cluster_id', 'c')}"  # global benzersiz (bloklar arası c1/c2 çakışmasın)
                    c["source"] = "llm"
                    c["tier"] = 2
                    # member_ids (v-etiketleri) -> gercek isim varyantlari
                    c["members"] = _members_from_labels(c, by_label, "name_variants")
                    if len(_distinct_variants(c)) < 2:
                        continue  # tek varyant — birleştirilecek bir şey yok, boş kart olmasın
                    all_clusters.append(c)
                for u in resp.get("uncertain", []):
                    u["block_key"] = block["key"]
                    all_uncertain.append(u)

                ctx.progress(0.15 + 0.8 * ((i + 1) / len(llm_blocks)))
        finally:
            cache.close()
        if orcid_merges or orcid_conflicts:
            ctx.log(f"ORCID: {orcid_merges} auto-merge, {orcid_conflicts} conflict (distinct people)")

    ctx.progress(0.7)

    # 3) Ayrıştırma (split) önerileri — aynı isim, alan bakımından ayrık gruplar
    ctx.log("Author splitting analysis (same spelling, different field)...")
    splits = await _compute_author_splits(ctx, project_dir, df)
    ctx.progress(0.9)

    # 4) Önerileri kaydet (henüz uygulanmadı — onay bekliyor)
    proposals_path = project_dir / "disambiguation_authors.json"
    proposals = {
        "kind": "authors",
        "generated_at": time.time(),
        "auto_approve_threshold": settings.disambiguation_auto_approve_threshold,
        "clusters": all_clusters,
        "splits": splits,
        "uncertain": all_uncertain,
    }
    proposals_path.write_text(json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.log(
        f"Merging: {len(all_clusters)} proposals ({auto_count} automatic + {len(all_clusters) - auto_count} LLM); "
        f"Splitting: {len(splits)} proposals"
    )

    # 5) Write-back — ambigu satırlarda DOI'den çekilen OI/RI/ROR/CC'yi veriye kalıcı
    #    yaz (fetch boşa gitmesin; sonra Fill-all gerekmesin). Yalnız boş hücrelere.
    snap_rel = None
    if settings.disambiguation_orcid_persist and not ctx.cancelled:
        ambiguous_rows: list[int] = []
        for b in llm_blocks:
            for m in b.get("members", []):
                ambiguous_rows.extend(m.get("records", []))
        for s in splits:
            for g in s.get("groups", []):
                ambiguous_rows.extend(g.get("records", []))
        if ambiguous_rows:
            df_before = df.copy(deep=True)
            wb = await asyncio.to_thread(
                orcid_mod.enrich_rows_from_doi, df, ambiguous_rows,
                email=(settings.crossref_email or None),
            )
            orcid_stats["rows_filled"] = wb["rows_filled"]
            orcid_stats["dois_fetched"] = wb["dois_fetched"]
            if wb["rows_filled"]:
                src = merger.merged_dataset_path(project_id)
                if src is not None:
                    snap_rel = _snapshot(project_dir, df_before, "orcid_enrich")
                    await asyncio.to_thread(df.to_excel, src, index=False)
                    try:
                        from services.filter_engine import _DF_CACHE
                        _DF_CACHE.clear()
                    except Exception:
                        pass
                    storage.touch_project(project_id)
                    ctx.log(f"ORCID write-back: {wb['rows_filled']} rows enriched (OI/ROR/CC)")

    ctx.progress(1.0)
    return {
        "task": "authors",
        "candidates": int(sum(len(b.get("members", [])) + len(b.get("auto", [])) for b in blocks)),
        "clusters_proposed": len(all_clusters),
        "splits_proposed": len(splits),
        "auto": auto_count,
        "uncertain": len(all_uncertain),
        "orcid_rows_filled": orcid_stats["rows_filled"],
        "dois_fetched": orcid_stats["dois_fetched"],
        "snapshot": str(snap_rel.relative_to(storage.settings.storage_path)) if snap_rel else None,
    }


async def run_affiliation_disambiguation(
    ctx: JobContext, project_id: str, mode: str = "auto",
    max_records: int | None = None,
) -> dict[str, Any]:
    project_dir = _project_dir(project_id)
    df = await asyncio.to_thread(_load_merged, project_id)
    ctx.log(f"Loaded: {len(df)} records")
    if max_records and max_records > 0 and len(df) > max_records:
        df = df.head(max_records)
        ctx.log(f"Test/cost mode: limited to first {max_records} records")
    ctx.log(f"Mode: {mode}")
    ctx.progress(0.05)

    ctx.log("Affiliation blocking + similarity analysis...")
    blocks = await run_cpu(
        build_affiliation_blocks, df,
        settings.disambiguation_blocking_threshold, mode,
        settings.disambiguation_auto_approve_threshold,
        should_cancel=lambda: ctx.cancelled,
    )
    ctx.log(f"{len(blocks)} candidate blocks found")
    ctx.progress(0.15)

    if not blocks:
        ctx.log("No affiliation variants to merge.")
        return {"task": "affiliations", "candidates": 0, "clusters": [], "uncertain": []}

    all_clusters: list[dict] = []
    all_uncertain: list[dict] = []

    # Tier 1 — deterministik (yüksek string benzerliği, LLM'siz)
    auto_count = 0
    for block in blocks:
        for ac in block.get("auto", []):
            variants = ac.get("variants", [])
            all_clusters.append({
                "cluster_id": ac["id"],
                "block_key": block["key"],
                "members": [ac],
                "canonical_name": variants[0] if variants else None,
                "confidence": 1.0,
                "source": "deterministic",
                "tier": 1,
                "reason": "High string similarity (automatic)",
            })
            auto_count += 1
    if auto_count:
        ctx.log(f"Tier 1 (automatic, no LLM): {auto_count} proposals")

    # Tier 2 — yalnız belirsiz blokları LLM'e
    llm_blocks = [b for b in blocks if b.get("members")]
    ctx.log(f"Tier 2 (LLM review): {len(llm_blocks)} blocks")
    if llm_blocks:
        client = _client()
        cache = DisambiguationCache(project_dir)
        try:
            for i, block in enumerate(llm_blocks):
                if ctx.cancelled:
                    break
                items, by_label = _label_for_llm(block["members"], "variants")
                payload = {"task": "affiliation_normalization", "candidates": items}
                cache_key = cache.hash_key(payload)
                cached = cache.get(cache_key)
                if cached:
                    resp = cached
                else:
                    try:
                        resp = await client.chat_json_async(AFFILIATION_SYSTEM_PROMPT, payload)
                        cache.set(cache_key, resp)
                    except Exception as e:
                        ctx.log(f"[{i+1}/{len(llm_blocks)}] ERROR: {e}")
                        continue
                for c in resp.get("clusters", []):
                    c["block_key"] = block["key"]
                    c["cluster_id"] = f"{block['key']}__{c.get('cluster_id', 'c')}"  # global benzersiz (bloklar arası c1/c2 çakışmasın)
                    c["source"] = "llm"
                    c["tier"] = 2
                    c["members"] = _members_from_labels(c, by_label, "variants")
                    if len(_distinct_variants(c)) < 2:
                        continue  # tek varyant — birleştirilecek bir şey yok, boş kart olmasın
                    all_clusters.append(c)
                for u in resp.get("uncertain", []):
                    u["block_key"] = block["key"]
                    all_uncertain.append(u)
                ctx.progress(0.15 + 0.8 * ((i + 1) / len(llm_blocks)))
        finally:
            cache.close()

    proposals_path = project_dir / "disambiguation_affiliations.json"
    proposals = {
        "kind": "affiliations",
        "generated_at": time.time(),
        "clusters": all_clusters,
        "uncertain": all_uncertain,
    }
    proposals_path.write_text(json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.log(f"{len(all_clusters)} proposals saved ({auto_count} automatic)")
    ctx.progress(1.0)
    return {
        "task": "affiliations",
        "clusters_proposed": len(all_clusters),
        "auto": auto_count,
        "uncertain": len(all_uncertain),
    }


async def _run_cluster_tool(
    ctx: JobContext, project_id: str, *,
    kind: str, blocks_builder, system_prompt: str, task: str,
    tier1_reason: str, mode: str = "auto", max_records: int | None = None,
) -> dict[str, Any]:
    """Affiliation-deseni standartlaştırma çalıştırıcısı (ülke / org rollup için ortak)."""
    project_dir = _project_dir(project_id)
    df = await asyncio.to_thread(_load_merged, project_id)
    ctx.log(f"Loaded: {len(df)} records")
    if max_records and max_records > 0 and len(df) > max_records:
        df = df.head(max_records)
    ctx.progress(0.05)

    blocks = await run_cpu(
        blocks_builder, df,
        settings.disambiguation_blocking_threshold, mode,
        settings.disambiguation_auto_approve_threshold,
        should_cancel=lambda: ctx.cancelled,
    )
    ctx.log(f"{len(blocks)} candidate blocks")
    ctx.progress(0.15)

    all_clusters: list[dict] = []
    all_uncertain: list[dict] = []
    auto_count = 0
    for block in blocks:
        for ac in block.get("auto", []):
            variants = ac.get("variants", [])
            all_clusters.append({
                "cluster_id": ac["id"],
                "block_key": block["key"],
                "members": [ac],
                "canonical_name": ac.get("canonical_name") or (variants[0] if variants else None),
                "confidence": 1.0,
                "source": "deterministic",
                "tier": 1,
                "reason": tier1_reason,
            })
            auto_count += 1
    if auto_count:
        ctx.log(f"Tier 1 (automatic): {auto_count} proposals")

    llm_blocks = [b for b in blocks if b.get("members")]
    if llm_blocks:
        client = _client()
        cache = DisambiguationCache(project_dir)
        try:
            for i, block in enumerate(llm_blocks):
                if ctx.cancelled:
                    break
                items, by_label = _label_for_llm(block["members"], "variants")
                payload = {"task": task, "candidates": items}
                ck = cache.hash_key(payload)
                cached = cache.get(ck)
                if cached:
                    resp = cached
                else:
                    try:
                        resp = await client.chat_json_async(system_prompt, payload)
                        cache.set(ck, resp)
                    except Exception as e:
                        ctx.log(f"[{i+1}/{len(llm_blocks)}] ERROR: {e}")
                        continue
                for c in resp.get("clusters", []):
                    c["block_key"] = block["key"]
                    c["cluster_id"] = f"{block['key']}__{c.get('cluster_id', 'c')}"  # global benzersiz (bloklar arası c1/c2 çakışmasın)
                    c["source"] = "llm"
                    c["tier"] = 2
                    c["members"] = _members_from_labels(c, by_label, "variants")
                    # Country'de tek token geçerlidir (bir ülke yazımını normalize et veya
                    # bir kuruma ülke ekle); diğerlerinde (org) birleştirme için >=2 gerekir.
                    if kind != "countries" and len(_distinct_variants(c)) < 2:
                        continue  # tek varyant — birleştirilecek bir şey yok, boş kart olmasın
                    all_clusters.append(c)
                for u in resp.get("uncertain", []):
                    u["block_key"] = block["key"]
                    all_uncertain.append(u)
                ctx.progress(0.15 + 0.8 * ((i + 1) / len(llm_blocks)))
        finally:
            cache.close()

    (project_dir / f"disambiguation_{kind}.json").write_text(
        json.dumps({"kind": kind, "generated_at": time.time(), "clusters": all_clusters, "uncertain": all_uncertain},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.progress(1.0)
    ctx.log(f"{len(all_clusters)} proposals saved ({auto_count} automatic)")
    return {"task": kind, "clusters_proposed": len(all_clusters), "auto": auto_count, "uncertain": len(all_uncertain)}


async def run_country_standardization(ctx: JobContext, project_id: str, mode: str = "auto", max_records: int | None = None) -> dict[str, Any]:
    return await _run_cluster_tool(
        ctx, project_id, kind="countries", blocks_builder=build_country_blocks,
        system_prompt=COUNTRY_SYSTEM_PROMPT, task="country_standardization",
        tier1_reason="Dictionary match (automatic)", mode=mode, max_records=max_records,
    )


async def run_org_rollup(ctx: JobContext, project_id: str, mode: str = "auto", max_records: int | None = None) -> dict[str, Any]:
    return await _run_cluster_tool(
        ctx, project_id, kind="organizations", blocks_builder=build_org_rollup,
        system_prompt=ORG_ROLLUP_SYSTEM_PROMPT, task="org_rollup",
        tier1_reason="Address parse -> parent institution (automatic)", mode=mode, max_records=max_records,
    )


def list_proposals(project_id: str, kind: str) -> dict[str, Any]:
    project_dir = _project_dir(project_id)
    fname = f"disambiguation_{kind}.json"
    p = project_dir / fname
    if not p.exists():
        return {"kind": kind, "clusters": [], "uncertain": []}
    return json.loads(p.read_text(encoding="utf-8"))


def apply_clusters(project_id: str, kind: str, approved: list[dict]) -> dict[str, Any]:
    """Onaylanmış cluster'ları orijinal datasete uygula.

    approved: list of {member_ids, canonical?: str}
    """
    project_dir = _project_dir(project_id)
    src = merger.merged_dataset_path(project_id)
    if src is None:
        raise HTTPException(409, "no_merged_data")
    df = pd.read_excel(src)

    # NOT: snapshot işin SONUNDA, yalnız gerçekten değişiklik yapılacaksa (affected>0)
    # alınır — 'sıfır değişiklik' apply'ları boş snapshot çöplüğü üretmesin.

    # Proposal listesinden member metadata'sını oku
    proposals = list_proposals(project_id, kind)
    cluster_lookup = {c.get("cluster_id"): c for c in proposals.get("clusters", [])}

    affected = 0
    if kind == "authors":
        # Onay: her cluster için canonical bir isim seç (varsayılan: ilk variant) ve diğerlerini değiştir
        if "AU" not in df.columns:
            raise HTTPException(400, "no_au_column")
        for entry in approved:
            cid = entry.get("cluster_id")
            cluster = cluster_lookup.get(cid)
            if not cluster:
                continue
            # Tüm name_variants'i topla (boşsa üyenin id'sine düş — LLM stub'ları)
            all_variants: list[str] = []
            for m in cluster.get("members", []):
                all_variants.extend(_member_variants(m, "name_variants"))
            canonical = entry.get("canonical") or (all_variants[0] if all_variants else None)
            if not canonical:
                continue
            import re as _re

            def _nkey(s: str) -> str:
                return _re.sub(r"\s+", " ", str(s or "")).strip().lower()

            # Boşluk-toleranslı: WoS AU yazımı tutarsız olabilir ('GAO S' vs 'GAO  S').
            variant_keys = {_nkey(v) for v in all_variants if _nkey(v) != _nkey(canonical)}
            if not variant_keys:
                continue

            def _repl(s: str) -> str:
                return ";".join(canonical if _nkey(x) in variant_keys else x.strip() for x in str(s).split(";"))

            before_col = df["AU"].astype(str)
            df["AU"] = before_col.apply(_repl)
            affected += int((df["AU"] != before_col).sum())

    elif kind == "affiliations":
        cols = [c for c in ("C1", "C1raw") if c in df.columns]
        if not cols:
            raise HTTPException(400, "no_c1_column")
        from bibex_core.modules.c1_utils import replace_affiliation_in_c1
        mapping: dict[str, str] = {}  # varyant affiliation -> kanonik (bileşen-tam)
        for entry in approved:
            cluster = cluster_lookup.get(entry.get("cluster_id"))
            if not cluster:
                continue
            all_variants: list[str] = []
            for m in cluster.get("members", []):
                all_variants.extend(_member_variants(m, "variants"))
            canonical = entry.get("canonical") or cluster.get("canonical_name") or (all_variants[0] if all_variants else None)
            if not canonical:
                continue
            for variant in set(all_variants):
                if variant and variant != canonical:
                    mapping[variant] = canonical
        if mapping:
            affected = _apply_c1_map(df, cols, lambda s: replace_affiliation_in_c1(s, mapping))

    elif kind == "countries":
        cols = [c for c in ("C1", "C1raw") if c in df.columns]
        if not cols:
            raise HTTPException(400, "no_c1_column")
        from bibex_core.modules.c1_utils import replace_country_in_c1, _is_org
        mapping: dict[str, str] = {}  # küçük-harf ülke-varyantı -> kanonik ülke
        for entry in approved:
            cluster = cluster_lookup.get(entry.get("cluster_id"))
            if not cluster:
                continue
            canon = entry.get("canonical") or cluster.get("canonical_name")
            if not canon:
                continue
            for m in cluster.get("members", []):
                for v in _member_variants(m, "variants"):
                    tok = str(v).strip()
                    # GÜVENLİK: kurum tokeni ülke replace'ine girmesin (kurum=ülke karmasını
                    # ve C1 bozulmasını önler). Country standardizasyonu YALNIZ ülke yazımını
                    # normalize eder; eksik ülke tamamlama ayrı API eylemiyle yapılır.
                    if tok and not _is_org(tok):
                        mapping[tok.lower()] = canon
        if mapping:
            affected = _apply_c1_map(df, cols, lambda s: replace_country_in_c1(s, mapping))

    elif kind == "organizations":
        cols = [c for c in ("C1", "C1raw") if c in df.columns]
        if not cols:
            raise HTTPException(400, "no_c1_column")
        from bibex_core.modules.c1_utils import replace_org_in_c1
        mapping: dict[str, str] = {}  # tam adres varyantı -> kanonik üst kurum
        for entry in approved:
            cluster = cluster_lookup.get(entry.get("cluster_id"))
            if not cluster:
                continue
            canon = entry.get("canonical") or cluster.get("canonical_name")
            if not canon:
                continue
            for m in cluster.get("members", []):
                for v in _member_variants(m, "variants"):
                    if v and str(v).strip():
                        mapping[str(v).strip()] = canon
        if mapping:
            affected = _apply_c1_map(df, cols, lambda s: replace_org_in_c1(s, mapping))
    else:
        raise HTTPException(400, f"Bilinmeyen kind: {kind}")

    # Kaydet (orijinal merged dosyanın üstüne yaz, ama snapshot var)
    # Hiçbir değişiklik yapılmadıysa: snapshot/yazım/audit YOK (boş apply no-op).
    if affected == 0:
        return {"kind": kind, "approved_count": len(approved), "replacements": 0, "snapshot": None}

    # Değişiklik var → ÖNCE snapshot (df'in güncel hâli yazımdan önce yedeklenir), sonra yaz.
    snap = _snapshot(project_dir, pd.read_excel(src), kind)
    df.to_excel(src, index=False)
    # Filter cache temizle
    from services.filter_engine import _DF_CACHE
    _DF_CACHE.clear()

    # Audit log
    audit_path = project_dir / f"disambiguation_audit_{kind}.json"
    audit: list[dict] = []
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.append({
        "applied_at": time.time(),
        "snapshot": str(snap.relative_to(storage.settings.storage_path)),
        "approved_count": len(approved),
        "replacements": affected,
        "details": approved,
    })
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    # Onaylanan cluster'ları proposals'tan düş — uygulananlar listede tekrar görünmesin.
    applied_ids = {e.get("cluster_id") for e in approved if e.get("cluster_id")}
    if applied_ids:
        props = list_proposals(project_id, kind)
        props["clusters"] = [c for c in props.get("clusters", []) if c.get("cluster_id") not in applied_ids]
        (project_dir / f"disambiguation_{kind}.json").write_text(
            json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    return {
        "kind": kind,
        "approved_count": len(approved),
        "replacements": affected,
        "snapshot": str(snap.relative_to(storage.settings.storage_path)),
    }


def apply_splits(project_id: str, approved: list[dict]) -> dict[str, Any]:
    """Onaylanan AYRIŞTIRMA önerilerini uygula — ilgili kayıtlarda yazar adına
    '(b)' '(c)' eki ekleyerek düğümleri ayır. En büyük grup (suffix boş) dokunulmaz.

    approved: split önerileri (split_id ile) veya tam obje (name + groups[]).
    """
    project_dir = _project_dir(project_id)
    src = merger.merged_dataset_path(project_id)
    if src is None:
        raise HTTPException(409, "no_merged_data")
    df = pd.read_excel(src)
    if "AU" not in df.columns:
        raise HTTPException(400, "no_au_column")

    # snapshot işin SONUNDA, yalnız affected>0 ise alınır (boş apply no-op).
    proposals = list_proposals(project_id, "authors")
    lookup = {s.get("split_id"): s for s in proposals.get("splits", [])}

    import re as _re

    def _nkey(s: str) -> str:
        # Boşluk-normalize anahtar: WoS AU yazımı tutarsız olabilir ('GAO S' vs 'GAO  S').
        return _re.sub(r"\s+", " ", str(s or "")).strip().lower()

    affected = 0
    for entry in approved:
        sp = lookup.get(entry.get("split_id")) or entry  # tam obje de kabul
        name = sp.get("name")
        if not name:
            continue
        name_key = _nkey(name)
        for g in sp.get("groups", []):
            suffix = (g.get("suffix") or "").strip()
            if not suffix:
                continue  # düz grup — dokunma
            new_name = f"{name} {suffix}"
            for rec in g.get("records", []):
                if not isinstance(rec, int) or rec < 0 or rec >= len(df):
                    continue
                cell = df.at[rec, "AU"]
                cur = "" if pd.isna(cell) else str(cell)
                # Boşluk-toleranslı token eşleşmesi (ham AU yazımı korunur, yalnız
                # eşleşen token yeni adla değiştirilir).
                new_parts, hit = [], False
                for x in cur.split(";"):
                    if _nkey(x) == name_key:
                        new_parts.append(new_name)
                        hit = True
                    else:
                        new_parts.append(x.strip())
                if hit:
                    df.at[rec, "AU"] = ";".join(new_parts)
                    affected += 1

    if affected == 0:
        return {"kind": "authors_split", "approved_count": len(approved), "replacements": 0, "snapshot": None}

    snap = _snapshot(project_dir, pd.read_excel(src), "authors_split")
    df.to_excel(src, index=False)
    try:
        from services.filter_engine import _DF_CACHE
        _DF_CACHE.clear()
    except Exception:
        pass

    audit_path = project_dir / "disambiguation_audit_authors_split.json"
    log: list[dict] = []
    if audit_path.exists():
        log = json.loads(audit_path.read_text(encoding="utf-8"))
    log.append({
        "applied_at": time.time(),
        "snapshot": str(snap.relative_to(storage.settings.storage_path)),
        "approved_count": len(approved),
        "replacements": affected,
    })
    audit_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    # Onaylanan split'leri proposals'tan düş — uygulananlar listede tekrar görünmesin.
    applied_ids = {e.get("split_id") for e in approved if e.get("split_id")}
    if applied_ids:
        props = list_proposals(project_id, "authors")
        props["splits"] = [s for s in props.get("splits", []) if s.get("split_id") not in applied_ids]
        (project_dir / "disambiguation_authors.json").write_text(
            json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    return {
        "kind": "authors_split",
        "approved_count": len(approved),
        "replacements": affected,
        "snapshot": str(snap.relative_to(storage.settings.storage_path)),
    }


def restore_snapshot(project_id: str, snapshot_relative: str) -> dict[str, Any]:
    project_dir = _project_dir(project_id)
    snap_path = storage.settings.storage_path / snapshot_relative
    if not snap_path.exists() or project_dir not in snap_path.parents:
        raise HTTPException(404, "snapshot_not_found")
    target = merger.merged_dataset_path(project_id)
    if target is None:
        raise HTTPException(409, "no_merged_data")
    import shutil
    shutil.copy2(snap_path, target)
    from services.filter_engine import _DF_CACHE
    _DF_CACHE.clear()
    return {"restored_from": snapshot_relative, "into": str(target.relative_to(storage.settings.storage_path))}
