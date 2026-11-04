"""Disambiguation pipeline orchestration: blocking, ORCID/LLM review, and apply.

Generates merge, split, and standardization proposals for authors,
affiliations, countries, and organizations, then applies approved
changes to the merged dataset with snapshots and audit logging.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from fastapi import HTTPException

from config import settings
from jobs.runner import JobContext, run_cpu
from services import analyses, dataset_io, merger, storage
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
    """Disambiguation working directory = the ACTIVE ANALYSIS directory (not the project root).

    This way the proposals (disambiguation_*.json), snapshots and audits are kept inside
    the analysis they belong to; a new merge (new analysis) does NOT carry over the old
    proposals — an automatic clean start. If there is no analysis at all, it falls back
    to the project root (backward compatible).
    """
    if storage.get_project(project_id) is None:
        raise HTTPException(404, "project_not_found")
    return analyses.work_dir(project_id)


def _load_merged(project_id: str) -> pd.DataFrame:
    p = merger.merged_dataset_path(project_id)
    if p is None:
        raise HTTPException(409, "no_merged_data")
    return dataset_io.read_dataset(p)


def _distinct_variants(cluster: dict) -> set[str]:
    """The set of distinct spellings/variants that a cluster merges.

    A member object carries `name_variants` (author) or `variants`
    (affiliation/org/country); if it has neither, the member's own `id` counts as its
    single variant. <2 variants = nothing to merge (single-member LLM cluster) → not
    shown as a proposal.
    """
    out: set[str] = set()
    for m in cluster.get("members", []):
        vs = m.get("name_variants") or m.get("variants") or ([m["id"]] if m.get("id") else [])
        out.update(v for v in vs if v)
    return out


def _label_for_llm(members: list[dict], variant_key: str,
                   context_keys: tuple = ()) -> tuple[list[dict], dict]:
    """Label EVERY VARIANT individually as v1..vN and send them to the LLM.

    Why variant level (not candidate level): with candidate-level labelling, the LLM
    SUB-INDEXED the variants inside a single multi-variant candidate ('v1') as 'v1_10'
    and the like; those labels did not match the candidate id and the card showed up
    as 'v1_10'. Once every variant gets its own 'vN' label, the LLM no longer needs to
    sub-index; the returned response maps back directly.

    Each item: {id:'vN', <name|text>: variant, **context}. Returns:
    (LLM items, {label: raw variant string}).
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
    """Convert the member_ids (v-labels) in the LLM response into the REAL variant
    strings; create a single member for each variant. Unknown label -> raw string
    (last resort)."""
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
    """A member's variant list; if empty, fall back to the member's own 'id' (LLM
    members most often carry the variant in the 'id' field)."""
    vs = m.get(key) or ([m["id"]] if m.get("id") else [])
    return [v for v in vs if v]


def _apply_c1_map(df: pd.DataFrame, cols: list[str], fn) -> int:
    """Apply fn to the C1/C1raw columns and return the number of rows that ACTUALLY changed.

    It used to be affected=len(mapping): even when no match hit at all, it reported a
    non-zero number (it would tell the user '109 changed' while the data stayed the
    same). Now only the rows that actually changed are counted (in any column)."""
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
    stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
    p = snaps / f"pre_{kind}_{stamp}.parquet"
    dataset_io.atomic_write_dataset(df, p)
    return p


# ── ORCID resolution does SYNCHRONOUS network I/O (orcids_for_candidate → fetch_orcids_for_doi →
# requests). It must therefore NEVER be called on the event loop without await: otherwise
# the loop is blocked during a scan and cancel/navigation/export FREEZE (the separate root
# cause of test finding #7). The helpers below are moved onto a thread with
# asyncio.to_thread; they exit early on cancellation via should_cancel. ──
def _resolve_member_orcid_sets(df, members, email, allow_api, should_cancel=None) -> list[set]:
    """Per-member ORCID sets (df-first + optional on-demand API). Must be run in a thread."""
    out: list[set] = []
    for m in members:
        if should_cancel and should_cancel():
            break
        out.append(orcid_mod.orcids_for_candidate(df, m, email=email, allow_api=allow_api))
    return out


def _resolve_split_group_sets(df, groups, name, email, allow_api, should_cancel=None) -> list[set]:
    """Per-group ORCID sets of a split proposal. Must be run in a thread."""
    out: list[set] = []
    for g in groups:
        if should_cancel and should_cancel():
            break
        cand = {"name_variants": [name], "records": g.get("records", [])}
        out.append(orcid_mod.orcids_for_candidate(df, cand, email=email, allow_api=allow_api))
    return out


async def _compute_author_splits(ctx: JobContext, project_dir: Path, df) -> list[dict]:
    """Author SPLIT proposals. Tier 1 (clear, field-disjoint) is deterministic;
    Tier 2 (borderline, some records have no field) goes to LLM arbitration — if the LLM
    says 'keep', the proposal is dropped."""
    splits = await run_cpu(build_author_splits, df, should_cancel=lambda: ctx.cancelled)
    if not splits:
        ctx.log("Splitting: no names to split.")
        return []

    # ── ORCID override: are the same-name, field-disjoint groups really different people? ──
    # Resolve ORCID per group (df-first, on-demand API if missing). ORCID is the gold standard:
    #   • A single shared ORCID spanning ≥2 groups → ONE interdisciplinary person → NEVER split.
    #   • Groups with different ORCIDs → definitely different people → strong split (no LLM).
    email = settings.crossref_email or None
    allow_api = settings.disambiguation_orcid_lookup
    orcid_keep = 0
    orcid_split = 0
    for s in splits:
        if ctx.cancelled:
            break
        # SYNCHRONOUS network I/O → run it on a thread (don't block the event loop;
        # keep cancel/nav/export from freezing).
        group_sets = await asyncio.to_thread(
            _resolve_split_group_sets, df, s.get("groups", []), s["name"], email, allow_api,
            lambda: ctx.cancelled,
        )
        known = [gs for gs in group_sets if gs]
        if len(known) >= 2:
            # Do all the known groups share a common ORCID?
            if set.intersection(*known):
                s["decision"] = "keep"        # same person → do not split
                s["source"] = "orcid"
                s["reason"] = "Same ORCID across groups — one interdisciplinary person"
                orcid_keep += 1
            else:
                s["decision"] = "split"       # different ORCIDs → definitely split
                s["source"] = "orcid"
                s["tier"] = 1
                s["confidence"] = 1.0
                s["reason"] = "Different ORCID per group — distinct people"
                orcid_split += 1
    if orcid_keep or orcid_split:
        ctx.log(f"ORCID split: {orcid_split} confirmed, {orcid_keep} kept (same person)")

    # Proposals with no ORCID decision (source != 'orcid') fall back to the old flow
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
            client = None  # No API key → leave it for manual review
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

    # Drop the 'keep' proposals (LLM or ORCID: a single interdisciplinary person) from the list.
    # This includes the ones decided via ORCID (split) — filter over all of splits.
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

    # 1.5) Tier 1 — deterministic auto-merge proposals (no LLM, confidence=1.0)
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

    # ORCID write-back statistics (Part 2d)
    orcid_stats = {"rows_filled": 0, "dois_fetched": 0}
    email = settings.crossref_email or None
    allow_api = settings.disambiguation_orcid_lookup

    # 2) Tier 2 — uncertain blocks. ORCID decision FIRST (df-first, on-demand API if missing),
    #    then the LLM if needed. ORCID gold standard: same=merge, different=separate.
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
                # 2.1) ORCID pre-pass — resolve the ORCID set per member (df-first + on-demand).
                # SYNCHRONOUS network I/O → run it on a thread (don't block the event loop;
                # otherwise cancel/navigation/export FREEZE during the scan — an additional
                # root cause of #7).
                orcid_sets = await asyncio.to_thread(
                    _resolve_member_orcid_sets, df, members, email, allow_api,
                    lambda: ctx.cancelled,
                )
                verdict = orcid_mod.classify_by_orcid(orcid_sets)

                if verdict == "merge":
                    # All members with a known ORCID are the same person → Tier 1 auto-merge (no LLM)
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
                    continue  # don't go to the LLM

                if verdict == "conflict":
                    # Disjoint ORCIDs → different people → NO merge (don't go to the LLM)
                    orcid_conflicts += 1
                    block_names = sorted({v for m in members for v in (m.get("name_variants") or [])})
                    all_uncertain.append({
                        "block_key": block["key"],
                        "names": block_names,
                        "reason": "Different ORCIDs — distinct people (not merged)",
                    })
                    ctx.progress(0.15 + 0.8 * ((i + 1) / len(llm_blocks)))
                    continue

                # 2.2) ORCID inconclusive → LLM arbitration (existing flow). If the LLM is not
                #      configured, leave the block for manual review (so the job doesn't crash).
                if client is None:
                    try:
                        client = _client()
                    except HTTPException:
                        client = False  # no key — the LLM is not used in this run
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
                    c["cluster_id"] = f"{block['key']}__{c.get('cluster_id', 'c')}"  # globally unique (so c1/c2 don't collide across blocks)
                    c["source"] = "llm"
                    c["tier"] = 2
                    # member_ids (v-labels) -> real name variants
                    c["members"] = _members_from_labels(c, by_label, "name_variants")
                    if len(_distinct_variants(c)) < 2:
                        continue  # single variant — nothing to merge, so don't create an empty card
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

    # 3) Split proposals — same name, groups that are disjoint in terms of field
    ctx.log("Author splitting analysis (same spelling, different field)...")
    splits = await _compute_author_splits(ctx, project_dir, df)
    ctx.progress(0.9)

    # 4) Save the proposals (not applied yet — awaiting approval)
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

    # 5) Write-back — persist the OI/RI/ROR/CC fetched from the DOI for ambiguous rows
    #    into the data (so the fetch isn't wasted and Fill-all isn't needed afterwards).
    #    Empty cells only.
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
                src = await asyncio.to_thread(merger.merged_dataset_path, project_id)
                if src is not None:
                    snap_rel = _snapshot(project_dir, df_before, "orcid_enrich")
                    await asyncio.to_thread(dataset_io.atomic_write_dataset, df, src)
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

    # Tier 1 — deterministic (high string similarity, no LLM)
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

    # Tier 2 — send only the uncertain blocks to the LLM
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
                    c["cluster_id"] = f"{block['key']}__{c.get('cluster_id', 'c')}"  # globally unique (so c1/c2 don't collide across blocks)
                    c["source"] = "llm"
                    c["tier"] = 2
                    c["members"] = _members_from_labels(c, by_label, "variants")
                    if len(_distinct_variants(c)) < 2:
                        continue  # single variant — nothing to merge, so don't create an empty card
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
    """Affiliation-pattern standardization runner (shared by country / org rollup)."""
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
                    c["cluster_id"] = f"{block['key']}__{c.get('cluster_id', 'c')}"  # globally unique (so c1/c2 don't collide across blocks)
                    c["source"] = "llm"
                    c["tier"] = 2
                    c["members"] = _members_from_labels(c, by_label, "variants")
                    # For Country a single token is valid (normalize a country spelling or
                    # add a country to an institution); for the others (org) >=2 are needed to merge.
                    if kind != "countries" and len(_distinct_variants(c)) < 2:
                        continue  # single variant — nothing to merge, so don't create an empty card
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
    """Apply the approved clusters to the original dataset.

    approved: list of {member_ids, canonical?: str}
    """
    project_dir = _project_dir(project_id)
    src = merger.merged_dataset_path(project_id)
    if src is None:
        raise HTTPException(409, "no_merged_data")
    df = dataset_io.read_dataset(src)

    # NOTE: the snapshot is taken at the END of the job, and only if a change is actually
    # going to be made (affected>0) — so that 'zero change' applies don't produce a
    # pile of empty snapshots.

    # Read the member metadata from the proposal list
    proposals = list_proposals(project_id, kind)
    cluster_lookup = {c.get("cluster_id"): c for c in proposals.get("clusters", [])}

    affected = 0
    if kind == "authors":
        # Approval: pick a canonical name for each cluster (default: the first variant) and replace the others
        if "AU" not in df.columns:
            raise HTTPException(400, "no_au_column")
        for entry in approved:
            cid = entry.get("cluster_id")
            cluster = cluster_lookup.get(cid)
            if not cluster:
                continue
            # Collect all name_variants (if empty, fall back to the member's id — LLM stubs)
            all_variants: list[str] = []
            for m in cluster.get("members", []):
                all_variants.extend(_member_variants(m, "name_variants"))
            canonical = entry.get("canonical") or (all_variants[0] if all_variants else None)
            if not canonical:
                continue
            import re as _re

            def _nkey(s: str) -> str:
                return _re.sub(r"\s+", " ", str(s or "")).strip().lower()

            # Whitespace-tolerant: the WoS AU spelling can be inconsistent ('GAO S' vs 'GAO  S').
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
        mapping: dict[str, str] = {}  # variant affiliation -> canonical (component-complete)
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
        mapping: dict[str, str] = {}  # lowercase country variant -> canonical country
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
                    # SAFETY: keep institution tokens out of the country replace (prevents
                    # an institution=country mix-up and C1 corruption). Country
                    # standardization ONLY normalizes the country spelling; completing a
                    # missing country is done by a separate API action.
                    if tok and not _is_org(tok):
                        mapping[tok.lower()] = canon
        if mapping:
            affected = _apply_c1_map(df, cols, lambda s: replace_country_in_c1(s, mapping))

    elif kind == "organizations":
        cols = [c for c in ("C1", "C1raw") if c in df.columns]
        if not cols:
            raise HTTPException(400, "no_c1_column")
        from bibex_core.modules.c1_utils import replace_org_in_c1
        mapping: dict[str, str] = {}  # full-address variant -> canonical parent institution
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

    # Save (overwrite the original merged file, but a snapshot exists)
    # If no change was made: NO snapshot/write/audit (an empty apply is a no-op).
    if affected == 0:
        return {"kind": kind, "approved_count": len(approved), "replacements": 0, "snapshot": None}

    # There is a change → snapshot FIRST (df's current state is backed up before the write), then write.
    snap = _snapshot(project_dir, dataset_io.read_dataset(src), kind)
    dataset_io.atomic_write_dataset(df, src)
    # Clear the filter cache
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

    # Remove the approved clusters from the proposals — so applied ones don't show up in the list again.
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
    """Apply the approved SPLIT proposals — separate the nodes by appending a '(b)' '(c)'
    suffix to the author name in the relevant records. The largest group (empty suffix)
    is left untouched.

    approved: split proposals (by split_id) or a full object (name + groups[]).
    """
    project_dir = _project_dir(project_id)
    src = merger.merged_dataset_path(project_id)
    if src is None:
        raise HTTPException(409, "no_merged_data")
    df = dataset_io.read_dataset(src)
    if "AU" not in df.columns:
        raise HTTPException(400, "no_au_column")

    # The snapshot is taken at the END of the job, only if affected>0 (an empty apply is a no-op).
    proposals = list_proposals(project_id, "authors")
    lookup = {s.get("split_id"): s for s in proposals.get("splits", [])}

    import re as _re

    def _nkey(s: str) -> str:
        # Whitespace-normalized key: the WoS AU spelling can be inconsistent ('GAO S' vs 'GAO  S').
        return _re.sub(r"\s+", " ", str(s or "")).strip().lower()

    affected = 0
    for entry in approved:
        sp = lookup.get(entry.get("split_id")) or entry  # a full object is also accepted
        name = sp.get("name")
        if not name:
            continue
        name_key = _nkey(name)
        for g in sp.get("groups", []):
            suffix = (g.get("suffix") or "").strip()
            if not suffix:
                continue  # plain group — leave it alone
            new_name = f"{name} {suffix}"
            for rec in g.get("records", []):
                if not isinstance(rec, int) or rec < 0 or rec >= len(df):
                    continue
                cell = df.at[rec, "AU"]
                cur = "" if pd.isna(cell) else str(cell)
                # Whitespace-tolerant token matching (the raw AU spelling is preserved,
                # only the matching token is replaced with the new name).
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

    snap = _snapshot(project_dir, dataset_io.read_dataset(src), "authors_split")
    dataset_io.atomic_write_dataset(df, src)
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

    # Remove the approved splits from the proposals — so applied ones don't show up in the list again.
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
    # NOT a byte copy but a format-converting read+write: the snapshot may be xlsx
    # (legacy) while the target is parquet; a raw copy would corrupt merged.parquet.
    dataset_io.atomic_write_dataset(dataset_io.read_dataset(snap_path), target)
    from services.filter_engine import _DF_CACHE
    _DF_CACHE.clear()
    return {"restored_from": snapshot_relative, "into": str(target.relative_to(storage.settings.storage_path))}
