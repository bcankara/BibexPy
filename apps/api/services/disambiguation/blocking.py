"""Deterministic candidate-group generation for entity disambiguation.

Builds blocked, tiered merge/split candidates before any LLM arbitration:
author name blocking, author splitting, affiliation and organization
clustering, and country-name standardization.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable, Iterable, Optional

import pandas as pd

from .similarity import jaro_winkler, name_initials, normalize_name


# Cooperative cancellation: heavy builders run in a thread (asyncio.to_thread).
# task.cancel() CANNOT KILL the thread — so the builder periodically checks
# `should_cancel()` inside its loop and exits early on cancellation. This way an
# orphaned thread does not lock up the shared pool (and the application).
# _CANCEL_EVERY: check frequency (every N rows/blocks), which keeps the cost of
# the call negligible.
CancelCheck = Optional[Callable[[], bool]]
_CANCEL_EVERY = 256


def _is_cancelled(should_cancel: CancelCheck, counter: int) -> bool:
    return bool(should_cancel) and (counter % _CANCEL_EVERY == 0) and should_cancel()


def _split_authors(value: str) -> list[str]:
    return [a.strip() for a in re.split(r";|\band\b", str(value or "")) if a.strip()]


# Split suffix: the "(b)", "(c)" ... or "(7)" marker that the system itself adds.
# This suffix denotes an author who was previously separated out as a DIFFERENT
# person — it must not be treated as a merge candidate (otherwise the scan undoes
# its own split). It covers both single-letter (b-z) and numeric suffixes (such as
# the "(7)" that build_author_splits produces for groups of 8+).
_SPLIT_SUFFIX_RE = re.compile(r"\s*\((?:[b-z]|\d+)\)\s*$", re.IGNORECASE)


def _split_suffix(name: str) -> str:
    """Return the split suffix of the name ('b', 'c', '7' ...) or empty ('' = plain/original)."""
    m = _SPLIT_SUFFIX_RE.search(str(name or ""))
    return m.group(0).strip().strip("()").lower() if m else ""


def _base_name(name: str) -> str:
    """Normalized name without the split suffix ('LIU L (b)' -> 'liu l').

    This is the CANONICAL norm used for clustering/similarity: the '(b)' suffix
    does not leak into the norm, so 'LIU L (b)' does not artificially resemble
    'LIU LEI' (cross-base leakage is prevented) and falls into the same block as
    the real base name.
    """
    return normalize_name(_SPLIT_SUFFIX_RE.sub("", str(name or "")))


def _split_affs(value: str) -> list[str]:
    parts = []
    for chunk in str(value or "").split(";"):
        chunk = re.sub(r"\[.*?\]", "", chunk).strip()
        if chunk:
            parts.append(chunk)
    return parts


# ───────────── Tier classification helpers ─────────────
# 3-tier confidence (Fellegi-Sunter 1969 match / possible-match / non-match):
#   Tier 1 (auto)  — very high similarity + shared context → merge without the LLM
#   Tier 2 (llm)   — medium similarity / uncertain → send to the LLM for arbitration
#   Tier 3 (skip)  — single variant / distant → leave untouched

# Field/category columns — a more stable signal than affiliation (a person changes
# affiliation but rarely changes their field). WC: WoS categories, SC: research
# areas, DE: author keywords, ID: Keywords Plus.
_FIELD_COLS = ("WC", "SC", "DE", "ID")


def _row_fields(row) -> set[str]:
    """Set of field/category/keyword tokens of a record (normalized).
    pandas NaN / empty / 'nan' values are filtered out so they do not produce bogus tokens.
    """
    out: set[str] = set()
    for col in _FIELD_COLS:
        val = row.get(col, "")
        if val is None:
            continue
        sval = str(val).strip()
        if not sval or sval.lower() == "nan":
            continue
        for tok in sval.split(";"):
            t = tok.strip().lower()
            if t and t != "nan":
                out.add(t)
    return out


def _field_signal(members: list[dict]) -> bool | None:
    """Field signal (based on subject/category rather than affiliation):
        None  → no field data at all → no signal, rely on name similarity
        True  → at least two mentions share a field/category → in favor of the same person
        False → field data exists but there is no overlap → different fields, same-name/different-person suspicion
    """
    sets = [m.get("fields") or set() for m in members]
    if not any(sets):
        return None
    n = len(members)
    for i in range(n):
        for j in range(i + 1, n):
            if sets[i] & sets[j]:
                return True
    return False


def _greedy_cluster(members: list[dict], threshold: float) -> list[list[dict]]:
    """Transitively cluster mentions by norm similarity (≥ threshold → same cluster).

    Exception: a mention that CARRIES a split suffix ('(b)', '(c)' ...) is an identity
    the system deliberately separated earlier → it is not put into any merge cluster
    (it stays on its own and is later dropped by the <2 variants filter). Otherwise the
    scan would undo its own split (e.g. try to merge 'LIU L (b)' with 'LIU LEI').
    """
    clustered: list[list[dict]] = []
    for m in members:
        if m.get("suffix"):
            clustered.append([m])  # separated identity → isolated, never a merge candidate
            continue
        placed = False
        for cluster in clustered:
            # If the target cluster contains a split identity, do not put it there either.
            if any(x.get("suffix") for x in cluster):
                continue
            if any(jaro_winkler(m["norm"], x["norm"]) >= threshold for x in cluster):
                cluster.append(m)
                placed = True
                break
        if not placed:
            clustered.append([m])
    return clustered


def _min_pairwise_jw(norms: set[str]) -> float:
    """Lowest pairwise Jaro-Winkler in a variant cluster (a measure of cluster cohesion)."""
    items = sorted(norms)
    if len(items) < 2:
        return 1.0
    lo = 1.0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            lo = min(lo, jaro_winkler(items[i], items[j]))
    return lo


def _candidate(key: str, ci: int, cluster: list[dict], variant_field: str, tier: int) -> dict:
    """Convert a cluster into a candidate object that fits the UI/LLM schema."""
    names = sorted({m["name"] for m in cluster})
    cand = {
        "id": f"{key}_{ci}",
        variant_field: names,
        "affiliations": sorted({a for m in cluster for a in m.get("affiliations", [])})[:10],
        "coauthors": sorted({c for m in cluster for c in m.get("coauthors", [])})[:10],
        "records": sorted({m["record"] for m in cluster})[:50],
        "tier": tier,
    }
    years = sorted({m["year"] for m in cluster if m.get("year")})
    cand["year_range"] = [years[0], years[-1]] if years else []
    return cand


def build_author_blocks(
    df: pd.DataFrame,
    sim_threshold: float = 0.85,
    mode: str = "auto",
    auto_threshold: float = 0.95,
    should_cancel: CancelCheck = None,
) -> list[dict]:
    """Group author names block by block; split them into tiers with pure threshold logic.

    3 tiers (Fellegi-Sunter match / possible-match / non-match):
      • Tier 1 (auto)  — name similarity ≥ auto_threshold (0.95) AND no field conflict
                         → merge suggestion without the LLM (block["auto"])
      • Tier 2 (llm)   — between 0.75–0.95, or ≥0.95 but the fields differ
                         → sent to LLM arbitration (block["members"])
      • Tier 3 (skip)  — single variant / < sim_threshold → leave untouched

    Field signal: WC/SC/DE/ID (subject/category). More stable than affiliation because
    a person changes affiliation but rarely changes their field. The `mode` parameter
    is kept for backward compatibility and does not affect behavior.

    Returns: blocks; each block has an optional "auto" (Tier 1) and "members" (Tier 2).
    """
    if "AU" not in df.columns:
        return []

    # surname+initial -> list of (record_idx, name, affiliations, coauthors, year)
    blocks: dict[str, list[dict]] = defaultdict(list)
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        authors = _split_authors(row.get("AU", ""))
        if not authors:
            continue
        affs = _split_affs(row.get("C1", "") or row.get("C1raw", ""))
        coauthors = authors
        year = str(row.get("PY", "") or "").strip()
        fields = _row_fields(row)  # WC/SC/DE/ID — field signal (more stable than affiliation)
        for au in authors:
            # The block key + similarity are computed WITHOUT the split suffix ('(b)');
            # otherwise '(b)' pollutes both the initials ('l b') and the norm, producing
            # artificial similarity and causing cross-base leakage.
            base_au = _SPLIT_SUFFIX_RE.sub("", au).strip()
            surname, initials = name_initials(base_au)
            if not surname:
                continue
            key = f"{surname}_{initials[:1]}" if initials else surname
            blocks[key].append({
                "record": int(idx),
                "name": au,
                "norm": normalize_name(base_au),  # canonical norm without the suffix
                "base": _base_name(au),           # = norm; explicit, for conflict detection
                "suffix": _split_suffix(au),      # '' / 'b' / 'c' / '7' ...
                "affiliations": affs,
                "coauthors": [c for c in coauthors if c != au],
                "year": year,
                "fields": fields,
            })

    # Each block: sim_threshold (0.75) components → pure threshold logic
    #   component ≥2 variants + min pairwise JW ≥ auto_threshold (0.95) → Tier 1 (automatic)
    #   component ≥2 variants + between 0.75–0.95                       → Tier 2 (LLM)
    #   single variant / <0.75                                          → Tier 3 (skip)
    out: list[dict] = []
    for key, members in blocks.items():
        # Block loop: there are few blocks but each one is heavy (_greedy_cluster is O(n²)) →
        # check on every block (no modulo); on cancellation return the result so far.
        if should_cancel and should_cancel():
            return out
        if len({m["norm"] for m in members}) < 2:
            continue  # no variants to merge

        auto_clusters: list[dict] = []
        llm_members: list[dict] = []
        for ci, comp in enumerate(_greedy_cluster(members, sim_threshold)):
            comp_norms = {m["norm"] for m in comp}
            if len(comp_norms) < 2:
                continue  # single-variant component → no merge (Tier 3)
            if _min_pairwise_jw(comp_norms) >= auto_threshold and _field_signal(comp) is not False:
                # Names ≥0.95 AND (shared field EXISTS or field data is ABSENT) → automatic (Tier 1).
                # If field data exists but does not overlap (_field_signal False) → falls to the AI.
                auto_clusters.append(_candidate(key, ci, comp, "name_variants", tier=1))
            else:
                llm_members.append(_candidate(key, ci, comp, "name_variants", tier=2))

        block: dict = {"key": key}
        if auto_clusters:
            block["auto"] = auto_clusters
        if llm_members:
            block["members"] = llm_members
        if block.get("auto") or block.get("members"):
            out.append(block)

    return out


def _field_components(recs: list[dict]) -> list[list[dict]]:
    """Split records into components by the 'share a common category' relation.
    If a record touches more than one component, those components merge (transitive).
    The resulting components are COMPLETELY DISJOINT from each other in terms of fields.
    """
    comps: list[list[dict]] = []
    for r in recs:
        rf = r["fields"]
        hit = [ci for ci, comp in enumerate(comps) if any(rf & x["fields"] for x in comp)]
        if not hit:
            comps.append([r])
        else:
            first = hit[0]
            comps[first].append(r)
            for ci in sorted(hit[1:], reverse=True):
                comps[first].extend(comps[ci])
                del comps[ci]
    return comps


def build_author_splits(df: pd.DataFrame, should_cancel: CancelCheck = None) -> list[dict]:
    """Author SPLITTING: if a name with the same spelling divides into record groups
    that are disjoint in terms of fields, they are probably DIFFERENT people → split suggestion.

    Tier 1 (clear): all records have field data and split into 2+ fully-disjoint groups
                  → split with the rationale "these fields are separate from those" (the
                  largest group stays plain, the others get a (b) (c)... suffix).
    Tier 2 (borderline): some records have no field data → ask the AI (split / keep).
    Otherwise (single group / chained shared fields) → no split.

    Returns: split suggestions; each one is {split_id, name, norm, tier, groups[], reason}.
    """
    if "AU" not in df.columns:
        return []
    mentions: dict[str, list[dict]] = defaultdict(list)
    name_of: dict[str, str] = {}
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        authors = _split_authors(row.get("AU", ""))
        if not authors:
            continue
        fields = _row_fields(row)
        for au in authors:
            # An already-split name ('ZIPF A (b)') must NOT become a split candidate AGAIN —
            # otherwise every scan would split it again and produce 'ZIPF A (b) (b)' (not idempotent).
            if _split_suffix(au):
                continue
            nm = normalize_name(au)
            if not nm:
                continue
            mentions[nm].append({"record": int(idx), "fields": fields})
            name_of.setdefault(nm, au)

    suffixes = ["", "(b)", "(c)", "(d)", "(e)", "(f)", "(g)"]
    out: list[dict] = []
    for norm, recs in mentions.items():
        if should_cancel and should_cancel():
            return out
        if len(recs) < 2:
            continue  # single record → nothing to split
        with_fields = [r for r in recs if r["fields"]]
        unknown = [r for r in recs if not r["fields"]]
        if len(with_fields) < 2:
            continue  # no field signal → cannot decide (skip)

        comps = _field_components(with_fields)
        if len(comps) < 2:
            continue  # single (chained) group → same person, no split

        comps.sort(key=len, reverse=True)  # the largest group stays plain
        groups = []
        for gi, comp in enumerate(comps):
            gfields = sorted({f for r in comp for f in r["fields"]})
            groups.append({
                "records": sorted({r["record"] for r in comp}),
                "fields": gfields[:8],
                "suffix": suffixes[gi] if gi < len(suffixes) else f"({gi})",
            })
        field_summ = " | ".join("[" + ", ".join(g["fields"][:3]) + "]" for g in groups)
        reason = f"Same name, {len(groups)} field-disjoint groups: {field_summ}"
        tier = 1
        if unknown:
            tier = 2
            reason += f"; also {len(unknown)} record(s) without field info"
        out.append({
            "split_id": f"{norm.replace(' ', '_')}_{len(out)}",
            "name": name_of[norm],
            "norm": norm,
            "tier": tier,
            "groups": groups,
            "reason": reason,
        })
    return out


def build_affiliation_blocks(
    df: pd.DataFrame,
    sim_threshold: float = 0.85,
    mode: str = "auto",
    auto_threshold: float = 0.95,
    should_cancel: CancelCheck = None,
) -> list[dict]:
    """Blocking of affiliation variants based on the first meaningful token. See build_author_blocks."""
    if "C1" not in df.columns and "C1raw" not in df.columns:
        return []

    # canonical form -> list of variants
    blocks: dict[str, list[dict]] = defaultdict(list)
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        affs = _split_affs(row.get("C1", "") or row.get("C1raw", ""))
        for aff in affs:
            norm = normalize_name(aff)
            if not norm:
                continue
            # first meaningful token (not a stopword)
            tokens = [t for t in norm.split() if t not in {"the", "of", "and", "for", "univ", "university"}]
            key = tokens[0] if tokens else norm[:5]
            blocks[key].append({
                "record": int(idx),
                "raw": aff,
                "norm": norm,
            })

    out: list[dict] = []
    for key, members in blocks.items():
        if should_cancel and should_cancel():
            return out
        if len({m["norm"] for m in members}) < 2:
            continue

        auto_clusters: list[dict] = []
        llm_members: list[dict] = []
        for ci, comp in enumerate(_greedy_cluster(members, sim_threshold)):
            comp_norms = {m["norm"] for m in comp}
            if len(comp_norms) < 2:
                continue  # single variant → no merge (Tier 3)
            cand = {
                "id": f"{key}_{ci}",
                "variants": sorted({m["raw"] for m in comp}),
                "records": sorted({m["record"] for m in comp})[:100],
            }
            # Affiliation: high string similarity alone is a strong signal (no context).
            if _min_pairwise_jw(comp_norms) >= auto_threshold:
                cand["tier"] = 1
                auto_clusters.append(cand)
            else:
                cand["tier"] = 2
                llm_members.append(cand)

        block: dict = {"key": key}
        if auto_clusters:
            block["auto"] = auto_clusters
        if llm_members:
            block["members"] = llm_members
        if block.get("auto") or block.get("members"):
            out.append(block)

    return out


def build_country_blocks(
    df: pd.DataFrame,
    sim_threshold: float = 0.85,
    mode: str = "auto",
    auto_threshold: float = 0.95,
    should_cancel: CancelCheck = None,
) -> list[dict]:
    """Country name standardization — the country component of C1.

    Tier 1 (deterministic): the ISO dictionary maps a variant to the canonical name (USA -> United States).
    Tier 2 (LLM): tokens not in the dictionary are clustered by similarity and sent to the arbiter.
    """
    from bibex_core.modules.c1_utils import canonical_country, extract_country_from_c1, _is_org

    if "C1" not in df.columns and "C1raw" not in df.columns:
        return []

    resolved: dict[str, dict] = {}     # canonical -> {variants, records}
    unresolved: dict[str, set] = {}    # token(lower) -> records
    unresolved_disp: dict[str, str] = {}
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        val = row.get("C1") or row.get("C1raw") or ""
        for tok in extract_country_from_c1(val):
            t = tok.strip()
            if not t:
                continue
            canon = canonical_country(t)
            if canon:
                r = resolved.setdefault(canon, {"variants": set(), "records": set()})
                r["variants"].add(t)
                r["records"].add(int(idx))
            else:
                # Do NOT send clearly-non-country tokens (institution / postal code) to the LLM —
                # this prevents the institution=country mix-up at the source. Cities go to the LLM
                # and the prompt puts them into 'uncertain' (not suggested). A missing country is
                # filled in by a separate API action.
                if _is_org(t) or any(ch.isdigit() for ch in t):
                    continue
                key = t.lower()
                unresolved.setdefault(key, set()).add(int(idx))
                unresolved_disp.setdefault(key, t)

    auto_clusters: list[dict] = []
    for canon, data in resolved.items():
        variants = sorted(data["variants"])
        if all(v.strip().lower() == canon.lower() for v in variants):
            continue  # already canonical — no change
        auto_clusters.append({
            "id": f"country_{re.sub(r'[^a-z0-9]+', '_', canon.lower()).strip('_')}",
            "variants": variants,
            "canonical_name": canon,
            "records": sorted(data["records"])[:1000],
            "tier": 1,
        })

    if should_cancel and should_cancel():
        return []
    members = [
        {"record": min(recs), "raw": unresolved_disp[k], "norm": normalize_name(unresolved_disp[k]), "_recs": recs}
        for k, recs in unresolved.items()
    ]
    llm_members: list[dict] = []
    for ci, comp in enumerate(_greedy_cluster(members, sim_threshold)):
        recs: set[int] = set()
        variants_u: set[str] = set()
        for m in comp:
            variants_u.add(m["raw"])
            recs |= m["_recs"]
        llm_members.append({
            "id": f"country_u_{ci}",
            "variants": sorted(variants_u),
            "records": sorted(recs)[:1000],
            "tier": 2,
        })

    block: dict = {"key": "country"}
    if auto_clusters:
        block["auto"] = auto_clusters
    if llm_members:
        block["members"] = llm_members
    return [block] if (auto_clusters or llm_members) else []


def build_org_rollup(
    df: pd.DataFrame,
    sim_threshold: float = 0.85,
    mode: str = "auto",
    auto_threshold: float = 0.95,
    should_cancel: CancelCheck = None,
) -> list[dict]:
    """Affiliation → parent institution rollup.

    Parses each C1 address and extracts the parent institution; clusters address
    variants that belong to the same institution. Tier 1: single clear address or high
    institution-name similarity. Tier 2: borderline variants go to LLM arbitration.
    Apply REPLACES the matching FULL address with the parent institution.
    """
    from collections import Counter
    from bibex_core.modules.c1_utils import parse_c1_address, split_c1_addresses

    if "C1" not in df.columns and "C1raw" not in df.columns:
        return []

    blocks: dict[str, list[dict]] = defaultdict(list)
    for _ri, (idx, row) in enumerate(df.iterrows()):
        if _is_cancelled(should_cancel, _ri):
            return []
        val = row.get("C1") or row.get("C1raw") or ""
        for addr in split_c1_addresses(val):
            info = parse_c1_address(addr)
            inst = info["institution"]
            if not inst:
                continue  # could not be parsed → skip (do not write a wrong rollup)
            norm = normalize_name(inst)
            if not norm:
                continue
            toks = [t for t in norm.split() if t not in {"the", "of", "and", "for", "univ", "university"}]
            key = toks[0] if toks else norm[:5]
            blocks[key].append({"record": int(idx), "raw": addr, "norm": norm, "parent": inst})

    out: list[dict] = []
    for key, members in blocks.items():
        if should_cancel and should_cancel():
            return out
        auto_clusters: list[dict] = []
        llm_members: list[dict] = []
        for ci, comp in enumerate(_greedy_cluster(members, sim_threshold)):
            parent = Counter(m["parent"] for m in comp).most_common(1)[0][0]
            variants = sorted({m["raw"] for m in comp})
            if len(variants) == 1 and variants[0].strip() == parent.strip():
                continue  # the single address already equals the parent institution
            cand = {
                "id": f"org_{key}_{ci}",
                "variants": variants,           # full original addresses (apply replaces these)
                "canonical_name": parent,       # suggested parent institution
                "records": sorted({m["record"] for m in comp})[:1000],
            }
            comp_norms = {m["norm"] for m in comp}
            if len(comp_norms) < 2 or _min_pairwise_jw(comp_norms) >= auto_threshold:
                cand["tier"] = 1
                auto_clusters.append(cand)
            else:
                cand["tier"] = 2
                llm_members.append(cand)

        block: dict = {"key": key}
        if auto_clusters:
            block["auto"] = auto_clusters
        if llm_members:
            block["members"] = llm_members
        if block.get("auto") or block.get("members"):
            out.append(block)

    return out
