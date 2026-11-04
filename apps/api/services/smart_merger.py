"""Smart Merge pipeline for deduplicating and combining bibliographic sources.

Runs a self-contained six-phase pipeline independent of the core merge path:
  1. Normalize  : DOI, title, year, surname, ISSN/PMID/UT
  2. Block      : group candidates by (year, surname initial); WoS blocks also meet
                  the Scopus blocks of the adjacent years
  3. Match      : staged rules (negative rules, DOI, PMID/UT, title similarity, journal+volume+page, borderline)
  4. Field merge: fixed per-field source preferences (WoS, Scopus, union, cross-fill)
  5. Audit      : write match, conflict, and borderline-queue reports
  6. Borderline : manual review UI plus optional LLM assistance behind a feature flag
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import pandas as pd

from bibex_core.cr_normalize import count_refs, normalize_cr
from config import settings
from jobs.runner import JobContext, run_cpu
from services import analyses, audit, dataset_io, filter_engine, storage
from services.disambiguation.similarity import jaro_winkler, name_initials, normalize_name


# ════════════════════════════════════════════════════════════════════════
#  CONSTANTS — Caputo (2024) field preferences
# ════════════════════════════════════════════════════════════════════════

# Reference: Caputo, A., Pizzi, S., Pellegrini, M. M., & Dabić, M. (2024).
# Automatic Merging of Scopus and Web of Science Data for Simplified and
# Effective Bibliometric Analysis. Annals of Data Science, 11(3), 1023–1047.
#
# These rules are FIXED — they cannot be changed from the UI, for academic
# reproducibility. Smart Merge v1.0 must produce the same result on each dataset.
FIELD_PREFERENCES: dict[str, str] = {
    # WoS-preferred (citation metrics and reference lists are cleaner in WoS)
    "TC": "wos",          # times cited
    "CR": "wos",          # cited references
    "NR": "wos",          # number of references
    # Scopus-preferred (abstracts and author lists are more complete in Scopus)
    "AB": "scopus",       # abstract
    "AU": "scopus",       # author short
    "AF": "scopus",       # author full
    "C1": "scopus",       # affiliations
    # Union (union of both, joined with ; and deduplicated)
    "DE": "union",        # author keywords
    "ID": "union",        # keywords plus
    # Cross-fill (if one is empty, fill it from the other)
    "WC": "cross_fill_wos_first",
    "SC": "cross_fill_wos_first",
}
DEFAULT_PREFERENCE = "wos_first"  # for all other fields

# Threshold constants
TITLE_EXACT_THRESHOLD = 0.92          # Stage 3
TITLE_BORDERLINE_LOW = 0.80           # Stage 5 lower bound
YEAR_TOLERANCE = 1                    # ±1 year
JOURNAL_SIMILARITY = 0.90             # Stage 4

# Stopwords for title normalization
STOPWORDS: set[str] = {
    "the", "a", "an", "of", "in", "on", "and", "or", "for", "to", "with",
    "by", "from", "as", "at", "is", "are", "was", "were", "be", "been",
}


# ════════════════════════════════════════════════════════════════════════
#  PHASE 1 — Normalization functions
# ════════════════════════════════════════════════════════════════════════

_DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)
# Underscore + the Unicode dash family (hyphen, non-breaking hyphen,
# figure/en/em/horizontal dash) are folded into a single canonical '-'
# character.
_DOI_SEP_RE = re.compile(r"[_\u2010-\u2015]")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")
_LATEX_RE = re.compile(r"\\[a-z]+\{[^}]*\}|\\[\\\\&%$#_{}~^]")
_ISSN_RE = re.compile(r"[^0-9Xx]")


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _present(v: Any) -> bool:
    """Is an IDENTIFIER value actually filled in? (None / NaN / "" / "nan" → False)

    "Empty" turns into three different things depending on the pandas version:
    None (object dtype), NaN (pandas 3's `str` dtype — and NaN is TRUTHY) and
    "" / "nan" (xlsx round-trip). A bare `if v` check treats NaN as filled and
    considers empty identifiers equal to each other; this causes records
    without a DOI to be collected under a single key and deleted, and genuine
    DOI-less ↔ DOI-bearing pairs to be vetoed.
    Identifier comparisons (DOI/PMID/UT/ISSN) use ONLY this helper.
    """
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s != "" and s.lower() not in ("nan", "none", "null")


def _clean_num(v: Any) -> str:
    """Comparison form of a numeric field (VL/BP/PG): '100.0' → '100'."""
    s = _to_str(v)
    if re.fullmatch(r"\d+\.0+", s):
        return s.split(".", 1)[0]
    return s


def normalize_doi(raw: Any) -> Optional[str]:
    """Reduce a DOI to its canonical form.

    Examples:
      'https://doi.org/10.1234/ABC'  → '10.1234/abc'
      'http://dx.doi.org/10.x'        → '10.x'
      '10.1234/abc/'                  → '10.1234/abc'
      '10.4103/jgid.jgid_12_19'       → '10.4103/jgid.jgid-12-19'
    """
    s = _to_str(raw)
    if not s:
        return None
    s = s.lower()
    # In dirty data, prefixes can be stacked ('doi:', a doubled
    # 'https://doi.org/'); strip them until the string stops changing. 4 rounds
    # practically cover every combination.
    for _ in range(4):
        prev = s
        s = _DOI_PREFIX_RE.sub("", s)
        s = re.sub(r"^doi\s*:\s*", "", s)
        s = re.sub(r"^doi\.org/", "", s)
        if s == prev:
            break
    s = s.rstrip("/. \t")
    # Separator folding: DOIs are compared in CANONICAL form, because the
    # indexers write the separator differently — WoS gives the same article as
    # '10.4103/jgid.jgid_12_19', Scopus as '10.4103/jgid.jgid-12-19'.
    # It is practically unheard of for a registrant to issue two DOIs that
    # differ ONLY by '_' vs '-' for DIFFERENT works; by contrast, the existing
    # behavior effectively bans genuine pairs: the moment doi_conflict()
    # returns True, the pair is vetoed without even being able to enter the
    # borderline queue (the decisive DOI rule operates on the canonical form).
    s = _DOI_SEP_RE.sub("-", s)
    if not s.startswith("10."):
        return None
    return s


def normalize_title(raw: Any) -> str:
    """Reduce a title to its canonical form.

    Unicode NFKD → ASCII → lower → punct strip → stopwords.
    """
    s = _to_str(raw)
    if not s:
        return ""
    # LaTeX macro cleanup
    s = _LATEX_RE.sub(" ", s)
    # Unicode normalize
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    # Stopword removal
    tokens = [t for t in s.split() if t not in STOPWORDS]
    return " ".join(tokens)


def normalize_year(raw: Any) -> Optional[int]:
    """'2023.0' / '2023' / 2023 → 2023; invalid values → None."""
    s = _to_str(raw)
    if not s:
        return None
    try:
        n = int(float(s))
        if 1900 <= n <= 2100:
            return n
        return None
    except (ValueError, TypeError):
        return None


def normalize_author_surname(raw: Any) -> str:
    """Normalize the first author's surname to uppercase ASCII.

    WoS 'Smith J;Lee K' and Scopus 'Smith, John A.;Lee, Kim' both give 'SMITH'.
    Returns '' if empty.
    """
    s = _to_str(raw)
    if not s:
        return ""
    # First author
    first = re.split(r"[;|]", s, maxsplit=1)[0]
    # Scopus format: 'Smith, John A.' → 'Smith John A'
    first = first.replace(",", " ")
    surname, _initials = name_initials(first)
    return surname.upper()


def normalize_issn(raw: Any) -> Optional[str]:
    """Reduce an ISSN to the 8-character (digits + X) form; invalid → None."""
    s = _to_str(raw)
    if not s:
        return None
    s = _ISSN_RE.sub("", s.upper())
    if len(s) == 8:
        return s
    return None


def normalize_id_token(raw: Any) -> Optional[str]:
    """Normalize a generic token such as PMID / UT: lowercase, whitespace strip."""
    s = _to_str(raw)
    if not s:
        return None
    s = s.lower().strip()
    return s or None


# ════════════════════════════════════════════════════════════════════════
#  PHASE 2 — Blocking
# ════════════════════════════════════════════════════════════════════════

def build_blocks(df: pd.DataFrame) -> dict[tuple[Optional[int], str], list[int]]:
    """key = (year, surname[0]) → list of row indices.

    If year is None, the row falls into the (None, surname[0]) block.
    If surname is empty, it falls into the (year, '') block.
    """
    blocks: dict[tuple[Optional[int], str], list[int]] = {}
    for idx, row in df.iterrows():
        year = row.get("_norm_year")
        surname = row.get("_norm_surname", "")
        first_letter = surname[0] if surname else ""
        key = (year, first_letter)
        blocks.setdefault(key, []).append(int(idx))
    return blocks


def dedup_within_source(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Eliminate identifier duplicates WITHIN the same source (UT/EID and DOI).

    Overlapping export files can bring in the same record twice (the same WoS
    UT), or two rows with the same DOI may exist. Cross-source matching only
    looks at WoS↔Scopus, so these were not being caught; moreover, with an
    in-source copy present, the greedy 1-1 matching left the second copy
    unmatched. In each identifier group the FULLEST row (the one with the
    highest number of non-empty cells) is kept.

    Returns: (deduplicated df — original index preserved, number of rows removed)
    """
    if df.empty:
        return df, 0
    # The completeness score is computed COLUMN BY COLUMN: df.astype(str)
    # materializes a string copy of the whole frame in one go (hundreds of MB
    # on a 100k-row object frame, and the single most intense numpy/SIMD burst
    # in the pipeline). Accumulating per column gives the same result with a
    # much lower peak memory.
    richness = pd.Series(0, index=df.index, dtype="int64")
    for col in df.columns:
        s = df[col]
        richness += s.notna().astype("int64") + (s.astype(str) != "").astype("int64")
    drop: set = set()
    for key_col in ("_norm_ut", "_norm_doi"):
        if key_col not in df.columns:
            continue
        best_for: dict[str, Any] = {}
        for idx in df.index:
            if idx in drop:
                continue
            v = df.at[idx, key_col]
            if not _present(v):
                continue
            prev = best_for.get(v)
            if prev is None:
                best_for[v] = idx
            elif richness[idx] > richness[prev]:
                drop.add(prev)
                best_for[v] = idx
            else:
                drop.add(idx)
    if not drop:
        return df, 0
    return df.loc[~df.index.isin(drop)], len(drop)


# ════════════════════════════════════════════════════════════════════════
#  PHASE 3 — Multi-stage matching
# ════════════════════════════════════════════════════════════════════════

_DOI_SEP_CHARS = frozenset("-._")


def _doi_sep_profile(suffix: str) -> tuple[str, dict[int, str]]:
    """Split a DOI suffix into a (separator-free core, position→separator) pair."""
    core: list[str] = []
    gaps: dict[int, str] = {}
    for ch in suffix:
        if ch in _DOI_SEP_CHARS:
            gaps[len(core)] = gaps.get(len(core), "") + ch
        else:
            core.append(ch)
    return "".join(core), gaps


def doi_separator_variants(canon_a: Optional[str], canon_b: Optional[str]) -> bool:
    """Do two CANONICAL DOIs differ only in separator spelling?

    The decisive-DOI rule stays in place: records whose normalized DOIs differ
    are different publications. The only exception is an indexer spelling the
    same DOI differently — '10.7189/jogh.12-05057' and '10.7189/JOGH.12.05057'
    are the same article. For such a pair the veto is lifted; but the DOI IS NOT
    COUNTED AS EVIDENCE, and the pair must get its evidence from the
    title+year+author stages (it never yields Stage 1).

    The rule is DELIBERATELY narrow. Deleting all separators and comparing
    (the skeleton approach) collides genuine and DIFFERENT publications: in a
    measurement, 34 verified pairs were found in Physical Review where the
    volume|page boundary shifts — '…/physrevc.5.350' (Michaud 1972, volume 5,
    p.350) and '…/physrevc.53.50' (Awasthi 1996, volume 53, p.50) are both
    registered, entirely separate articles. Therefore:
      • the registrant prefix (before the first '/') must be identical,
      • the core without separators must be identical,
      • every differing separator position must be either a one-to-one
        substitution (- ↔ . ↔ _), or an insertion/deletion at an internal
        boundary where at least one neighbor is NOT a digit.
    Separator insertion/deletion between two digits, insertion/deletion at an
    edge, and '--' ↔ '-' are REJECTED. Measurement: 34 of 34 different-publication
    pairs are rejected, 10 of 10 genuine spelling-difference pairs are accepted,
    0 false positives across 32,454 candidates.
    """
    if not canon_a or not canon_b or canon_a == canon_b:
        return False
    pa, _, sa = canon_a.partition("/")
    pb, _, sb = canon_b.partition("/")
    if pa != pb or not sa or not sb:
        return False
    ca, ga = _doi_sep_profile(sa)
    cb, gb = _doi_sep_profile(sb)
    if ca != cb:
        return False
    for i in set(ga) | set(gb):
        x, y = ga.get(i, ""), gb.get(i, "")
        if x == y:
            continue
        if len(x) == 1 and len(y) == 1:
            continue                      # one-to-one substitution: safe
        if len(x) > 1 or len(y) > 1:
            return False                  # '--' ↔ '-'
        if i == 0 or i == len(ca):
            return False                  # insertion/deletion at an edge
        if ca[i - 1].isdigit() and ca[i].isdigit():
            return False                  # digit|digit: partitioning shift
    return True


def doi_conflict(raw_a: Any, raw_b: Any) -> bool:
    """After normalization, are two raw DOIs both present and DIFFERENT?

    DOI IS DECISIVE: if the sanitized (normalized) DOIs differ, the records are
    NEVER the same publication — they are not even shown in the borderline
    (manual approval) list. If one side has no DOI, there is no conflict
    (False). `negative_rule_check` already filters these pairs out in new
    merges; this helper provides a retroactive safety layer when reading old
    queues (`list_borderline`).
    """
    a = normalize_doi(raw_a)
    b = normalize_doi(raw_b)
    if not a or not b or a == b:
        return False
    # If only the separator spelling differs, this is not a conflict; the veto
    # is not applied, so that the pair can enter manual review (borderline).
    return not doi_separator_variants(a, b)


def compute_match(w: dict, s: dict) -> Optional[dict]:
    """5-stage matching decision.

    Returns: None (no match) or {stage, stage_label, confidence, reason, doi, doi_variant,
    jw_title, year_diff, surname_match}
    """
    # ── Identifier HIERARCHY: DOI > PMID > ISSN ───────────────────────────
    # A DEFINITE equality at a higher level overrides a contradiction at a lower
    # level. E.g. WoS gives the print-ISSN, Scopus the e-ISSN — an ISSN
    # contradiction cannot veto a pair with the same DOI (old behavior:
    # negative_rule_check applied all levels at once and silently left the
    # same-DOI pair as a duplicate). A lower-level contradiction rejects only
    # when there is NO equality at a higher level.

    # DOI: conflict → never the same publication; equality → definite merge
    w_doi = w.get("_norm_doi")
    s_doi = s.get("_norm_doi")
    doi_note = ""
    doi_variant = False
    if _present(w_doi) and _present(s_doi) and w_doi != s_doi:
        if not doi_separator_variants(w_doi, s_doi):
            return None
        # Separator/spelling difference: the veto is lifted, the DOI is not counted
        # as evidence. The pair must go through the title/year/author stages
        # below — Stage 1 is not produced.
        doi_variant = True
        doi_note = f" | DOI spelling variant: {w_doi} <-> {s_doi}"
    elif _present(w_doi) and _present(s_doi):
        return {
            "stage": "1_doi_exact",
            "stage_label": "DOI exact",
            "confidence": 1.00,
            "reason": f"DOI exact: {w_doi}",
            "doi": w_doi,
            "doi_variant": False,
            "jw_title": None,
            "year_diff": None,
            "surname_match": None,
        }

    # PMID (if the DOI could not be compared): conflict → reject; equality → merge
    # (UT is not the same across databases, only PMID)
    w_pmid = w.get("_norm_pmid")
    s_pmid = s.get("_norm_pmid")
    if _present(w_pmid) and _present(s_pmid):
        if w_pmid != s_pmid:
            return None
        return {
            "stage": "2_pmid_exact",
            "stage_label": "PMID exact",
            "confidence": 0.99,
            "reason": f"PMID exact: {w_pmid}",
            "doi": "",
            "doi_variant": doi_variant,
            "jw_title": None,
            "year_diff": None,
            "surname_match": None,
        }

    # ISSN (journal-level safeguard): vetoes only the title stages
    w_issn = w.get("_norm_issn")
    s_issn = s.get("_norm_issn")
    if _present(w_issn) and _present(s_issn) and w_issn != s_issn:
        return None

    # Stage 3 — Title JW ≥ 0.92 + Year ±1 + Surname match
    w_title = w.get("_norm_title", "")
    s_title = s.get("_norm_title", "")
    if w_title and s_title:
        jw_title = jaro_winkler(w_title, s_title)
        w_year = w.get("_norm_year")
        s_year = s.get("_norm_year")
        year_diff = abs((w_year or 0) - (s_year or 0)) if (w_year is not None and s_year is not None) else None
        w_surname = w.get("_norm_surname", "")
        s_surname = s.get("_norm_surname", "")
        surname_match = bool(w_surname and s_surname and w_surname == s_surname)

        # Generic/short title safeguard: titles such as "Editorial", "Erratum",
        # "Preface" can belong to MORE THAN ONE different publication by the same
        # author in the same year (a journal's editor writes an editorial for
        # every issue). Such titles cannot automatically enter Stage 3; they are
        # merged only via an identifier (DOI/PMID) or Stage 4
        # (journal+volume+page).
        informative_title = len(w_title.split()) >= 3 or len(w_title) >= 15

        if (
            informative_title
            and jw_title >= TITLE_EXACT_THRESHOLD
            and year_diff is not None
            and year_diff <= YEAR_TOLERANCE
            and surname_match
        ):
            return {
                "stage": "3_title_year_surname",
                "stage_label": "Title+Year+Surname",
                "confidence": 0.95,
                "reason": f"JW(title)={jw_title:.3f} >= {TITLE_EXACT_THRESHOLD}, year_diff={year_diff}, surname '{w_surname}' matched" + doi_note,
                "doi": "",
                "doi_variant": doi_variant,
                "jw_title": round(jw_title, 4),
                "year_diff": year_diff,
                "surname_match": surname_match,
            }

        # Stage 4 — Journal + Volume + (Pages or BP) — for old publications without a DOI
        w_journal = w.get("_norm_journal", "")
        s_journal = s.get("_norm_journal", "")
        if w_journal and s_journal:
            jw_journal = jaro_winkler(w_journal, s_journal)
            # The xlsx path turns numeric fields into floats ("100.0"); WoS txt
            # gives strings ("100"). Without stripping the ".0" tail, the
            # comparison would always miss.
            w_vol = _clean_num(w.get("VL", ""))
            s_vol = _clean_num(s.get("VL", ""))
            w_bp = _clean_num(w.get("BP", ""))
            s_bp = _clean_num(s.get("BP", ""))
            w_pg = _clean_num(w.get("PG", ""))
            s_pg = _clean_num(s.get("PG", ""))
            page_match = (w_bp and s_bp and w_bp == s_bp) or (w_pg and s_pg and w_pg == s_pg)
            if (
                jw_journal >= JOURNAL_SIMILARITY
                and w_vol and s_vol and w_vol == s_vol
                and page_match
            ):
                return {
                    "stage": "4_journal_vol_page",
                    "stage_label": "Journal+Vol+Pages",
                    "confidence": 0.90,
                    "reason": f"JW(journal)={jw_journal:.3f}, vol={w_vol}, page_match=True" + doi_note,
                    "doi": "",
                    "doi_variant": doi_variant,
                    "jw_title": round(jw_title, 4),
                    "year_diff": year_diff,
                    "surname_match": surname_match,
                }

        # Stage 5 — Borderline (manual queue)
        if TITLE_BORDERLINE_LOW <= jw_title < TITLE_EXACT_THRESHOLD:
            # Confidence linear scale 0.70-0.85
            conf = 0.70 + (jw_title - TITLE_BORDERLINE_LOW) * (0.85 - 0.70) / (TITLE_EXACT_THRESHOLD - TITLE_BORDERLINE_LOW)
            return {
                "stage": "5_borderline",
                "stage_label": "Borderline (manual review)",
                "confidence": round(conf, 3),
                "reason": f"JW(title)={jw_title:.3f}, year_diff={year_diff}, surname_match={surname_match}" + doi_note,
                "doi": "",
                "doi_variant": doi_variant,
                "jw_title": round(jw_title, 4),
                "year_diff": year_diff,
                "surname_match": surname_match,
            }

    return None


def generate_candidates(
    wos_df: pd.DataFrame, scp_df: pd.DataFrame,
) -> list[tuple[float, int, int, dict]]:
    """Generate candidate (WoS, Scopus) pairs — IDENTIFIER-FIRST + blocking.

    Critical rule: every pair sharing the same normalized DOI (or PMID) is
    evaluated REGARDLESS of blocking. In the old behavior, candidates came
    only from (year, first letter of surname) blocks; when WoS and Scopus
    parsed the surname differently (e.g. 'RAHIM ZA' vs 'ABDUL RAHIM NR') or
    the year differed (early-access 2024 vs print 2025), the pair with the
    same DOI was NEVER compared and the DOI-exact stage never got its turn →
    duplicate records stayed in the dataset. Blocking now limits candidates
    only for the title-similarity stages.

    Returns: [(confidence, w_idx, s_idx, match), ...] sorted by descending confidence
    """
    candidates: list[tuple[float, int, int, dict]] = []
    seen_pairs: set[tuple[int, int]] = set()

    def consider(w_idx: int, s_idx: int) -> None:
        pair = (int(w_idx), int(s_idx))
        if pair in seen_pairs:
            return
        seen_pairs.add(pair)
        m = compute_match(wos_df.loc[w_idx].to_dict(), scp_df.loc[s_idx].to_dict())
        if m is not None:
            candidates.append((m["confidence"], pair[0], pair[1], m))

    # 1) Identifier indexes — DOI- and PMID-sharing pairs are block-independent
    for key_col in ("_norm_doi", "_norm_pmid"):
        if key_col not in wos_df.columns or key_col not in scp_df.columns:
            continue
        w_index: dict[str, list[int]] = {}
        for idx in wos_df.index:
            v = wos_df.at[idx, key_col]
            if _present(v):
                w_index.setdefault(v, []).append(int(idx))
        for idx in scp_df.index:
            v = scp_df.at[idx, key_col]
            if not _present(v):
                continue
            for w_idx in w_index.get(v, ()):
                consider(w_idx, int(idx))

    # 2) Blocking — candidate space for the title-similarity stages
    # Because the block key (year, first letter of surname) demands an EXACT
    # year, Stage 3's ±1 year tolerance practically never kicked in: genuine
    # pairs with an early-access/print year difference were not even
    # candidates. A WoS row is now also compared against the Scopus blocks of
    # the neighboring years (ONE-sided: the candidate count grows ~3×, not 9×).
    # Records with an unknown year keep the old behavior — opening None to
    # every year would blow up the candidate space.
    wos_blocks = build_blocks(wos_df)
    scp_blocks = build_blocks(scp_df)
    for (year, letter), w_idxs in wos_blocks.items():
        years = (year,) if year is None else (year - 1, year, year + 1)
        for y in years:
            s_idxs = scp_blocks.get((y, letter))
            if not s_idxs:
                continue
            for w_idx in w_idxs:
                for s_idx in s_idxs:
                    consider(w_idx, s_idx)

    candidates.sort(key=lambda x: -x[0])
    return candidates


# ════════════════════════════════════════════════════════════════════════
#  PHASE 4 — Field merge with Caputo 2024 preferences
# ════════════════════════════════════════════════════════════════════════

def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s == "" or s.lower() == "nan"


def _union_values(w_val: Any, s_val: Any, sep: str = "; ") -> str:
    """Join two values with ; and dedup them (case-insensitive)."""
    parts: list[str] = []
    seen: set[str] = set()
    for v in (w_val, s_val):
        if _is_empty(v):
            continue
        for token in re.split(r"\s*[;|]\s*", _to_str(v)):
            token = token.strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(token)
    return sep.join(parts)


def _apply_preference(field: str, w_val: Any, s_val: Any) -> tuple[Any, str]:
    """Apply the Caputo 2024 default preference for a field.

    Returns: (chosen_value, chosen_source) — source in {"wos","scopus","union","cross_fill","empty"}
    """
    pref = FIELD_PREFERENCES.get(field, DEFAULT_PREFERENCE)
    w_empty = _is_empty(w_val)
    s_empty = _is_empty(s_val)

    if w_empty and s_empty:
        return "", "empty"

    if pref == "wos":
        return (w_val, "wos") if not w_empty else (s_val, "scopus_fallback")
    if pref == "scopus":
        return (s_val, "scopus") if not s_empty else (w_val, "wos_fallback")
    if pref == "union":
        return _union_values(w_val, s_val), "union"
    if pref == "cross_fill_wos_first":
        if not w_empty:
            return w_val, "wos"
        return s_val, "scopus"
    if pref == "wos_first":
        if not w_empty:
            return w_val, "wos"
        return s_val, "scopus"
    # Unknown pref — default wos_first
    if not w_empty:
        return w_val, "wos"
    return s_val, "scopus"


def merge_pair_with_preferences(
    pair_id: str,
    w: dict,
    s: dict,
    all_columns: list[str],
) -> tuple[dict, list[dict]]:
    """Merge two records and log the conflicts.

    Returns: (merged_row, conflicts_list)
    """
    merged: dict[str, Any] = {}
    conflicts: list[dict] = []

    for col in all_columns:
        if col.startswith("_norm_"):
            continue  # internal columns
        w_val = w.get(col)
        s_val = s.get(col)
        chosen, source = _apply_preference(col, w_val, s_val)
        merged[col] = chosen

        # Conflict log: both sides are filled AND the values differ
        if not _is_empty(w_val) and not _is_empty(s_val):
            if _to_str(w_val).lower().strip() != _to_str(s_val).lower().strip():
                conflicts.append({
                    "pair_id": pair_id,
                    "field": col,
                    "wos_value": _to_str(w_val)[:200],
                    "scopus_value": _to_str(s_val)[:200],
                    "chosen_source": source,
                    "chosen_value": _to_str(chosen)[:200],
                    "preference_rule": FIELD_PREFERENCES.get(col, DEFAULT_PREFERENCE),
                })

    # DB labeling — merged record
    merged["DB"] = "BIBEXPY_SMART"
    merged["DB_Original"] = "ISI; SCOPUS"

    return merged, conflicts


# ════════════════════════════════════════════════════════════════════════
#  PHASE 5 — Writers (audit, conflict, borderline, lost, statistic)
# ════════════════════════════════════════════════════════════════════════

def _write_match_audit(rows: list[dict], out: Path) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)
    cols = ["pair_id", "wos_index", "scp_index", "doi", "stage", "stage_label",
            "confidence", "jw_title", "year_diff", "surname_match", "reason"]
    cols = [c for c in cols if c in df.columns]
    df[cols].to_excel(out, index=False)


def _write_conflict_log(conflicts: list[dict], out: Path) -> None:
    if not conflicts:
        return
    df = pd.DataFrame(conflicts)
    df.to_excel(out, index=False)


def _write_borderline_queue(items: list[dict], out: Path) -> None:
    if not items:
        return
    df = pd.DataFrame(items)
    df.to_excel(out, index=False)


def _write_lost_records(df: pd.DataFrame, out: Path) -> None:
    if df.empty:
        return
    # Drop the internal _norm_* columns
    cols = [c for c in df.columns if not c.startswith("_norm_")]
    df[cols].to_excel(out, index=False)


def _write_statistic_smart(
    total: int, wos_count: int, scp_count: int,
    merged_df: pd.DataFrame, out: Path,
) -> None:
    """Matches the existing Statistic.xlsx schema — General Stats + Field Stats sheets."""
    general = pd.DataFrame([{
        "Total Records": total,
        "WoS Records": wos_count,
        "Scopus Records": scp_count,
        "Merged Columns": len(merged_df.columns),
        "Common Columns": 0,  # placeholder
    }])

    # Field stats
    field_rows = []
    for col in merged_df.columns:
        if col.startswith("_norm_"):
            continue
        missing = int(merged_df[col].apply(_is_empty).sum())
        pct = (missing / total * 100) if total else 0
        if pct == 0:
            status = "Excellent"
        elif pct < 5:
            status = "Very Good"
        elif pct < 15:
            status = "Good"
        elif pct < 40:
            status = "Poor"
        else:
            status = "Very Poor"
        field_rows.append({
            "": col,
            "Description": col,
            "Missing Count": missing,
            "Missing %": round(pct, 2),
            "Status": status,
        })
    fields = pd.DataFrame(field_rows)

    # Atomic write: if the merge summary (GET /merge/summary) sees a half-written
    # file while reading this one, it silently falls through to the fallback path
    # and shows WoS/Scopus=0.
    tmp = out.with_name(out.name + f".{uuid4().hex[:8]}.tmp~")
    try:
        with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
            general.to_excel(writer, sheet_name="General Stats", index=False)
            fields.to_excel(writer, sheet_name="Field Stats", index=False)
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _refresh_statistic_after_borderline(adir: Path, df: pd.DataFrame) -> None:
    """Rewrite Statistic.xlsx to match the real dataset after a borderline acceptance.

    The merge summary and the method paragraph read their counts from
    Statistic.xlsx; since the file was written only at merge time, accepted
    pairs never showed up in it (the dataset dropped to 1,292 records, yet the
    text said 1,296). The input counts (WoS / Scopus) do not change; the total
    and field-completeness statistics are recomputed.
    """
    stat = adir / "Statistic.xlsx"
    if not stat.exists():
        legacy = adir / "Statistic_Smart.xlsx"
        if not legacy.exists():
            return
        stat = legacy
    gen = pd.read_excel(stat, sheet_name="General Stats")
    if gen.empty:
        return
    row = gen.iloc[0]
    wos_n = int(row.get("WoS Records", 0) or 0)
    scp_n = int(row.get("Scopus Records", 0) or 0)
    _write_statistic_smart(int(len(df)), wos_n, scp_n, df, stat)


def _append_conflict_log(path: Path, conflicts: list[dict]) -> int:
    """Append the field conflicts of accepted pairs to conflict_log.xlsx.

    Old rows of the same pair_id are discarded (so that re-accepting stays
    idempotent). Written atomically; a failure does not roll back the dataset
    decision — the caller catches it.
    """
    if not conflicts:
        return 0
    new = pd.DataFrame(conflicts)
    if path.exists():
        old = pd.read_excel(path)
        if "pair_id" in old.columns:
            old = old[~old["pair_id"].astype(str).isin(set(new["pair_id"].astype(str)))]
        new = pd.concat([old, new], ignore_index=True)
    tmp = path.with_name(path.name + f".{uuid4().hex[:8]}.tmp~")
    try:
        new.to_excel(tmp, index=False)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return len(conflicts)


# ════════════════════════════════════════════════════════════════════════
#  Borderline state management (JSON)
# ════════════════════════════════════════════════════════════════════════

def _borderline_state_path(project_id: str, adir: Optional[Path] = None) -> Optional[Path]:
    """Path of the borderline state file — in the active analysis folder."""
    if adir is None:
        adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        return None
    return adir / "borderline_state.json"


def _read_borderline_state(project_id: str, adir: Optional[Path] = None) -> dict[str, dict]:
    p = _borderline_state_path(project_id, adir)
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_borderline_state(project_id: str, state: dict[str, dict], adir: Optional[Path] = None) -> None:
    p = _borderline_state_path(project_id, adir)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _borderline_state_copy_path(snap_path: Path) -> Path:
    """Where the borderline state that belongs to a pre_borderline_accept snapshot is kept."""
    return snap_path.with_name(snap_path.name + ".borderline_state.json")


def restore_borderline_state(project_id: str, snap_path: Path) -> bool:
    """After a snapshot restore: put back the borderline state stored with that snapshot.

    Only pre_borderline_accept snapshots carry such a copy. Without it the restored dataset
    would hold both unmerged rows again while the state still says "accept", so the pair could
    never be merged again. Returns True when the state was rolled back.
    """
    copy_path = _borderline_state_copy_path(Path(snap_path))
    if not copy_path.exists():
        return False
    adir = analyses.get_active_analysis_dir(project_id)
    # The copy belongs to the analysis the snapshot was taken in (snapshots/ is its subfolder).
    if adir is None or copy_path.parent.parent.resolve() != Path(adir).resolve():
        return False
    try:
        state = json.loads(copy_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(state, dict):
        return False
    _write_borderline_state(project_id, state, adir=adir)
    return True


# ════════════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ════════════════════════════════════════════════════════════════════════

async def run_smart_merge(ctx: JobContext, project_id: str) -> dict[str, Any]:
    """Main Smart Merge workflow. Returns a result dict (for the audit hook).

    Creates a new analysis folder, writes the outputs into it, and finally
    marks it as the active analysis. On ANY failure, including a cancel (CancelledError is
    a BaseException, so `except Exception` never sees it), the unfinished folder is
    deleted: it has no completed_at and would otherwise be listed as a normal analysis.
    """
    progress: dict[str, Any] = {}
    try:
        return await _run_smart_merge(ctx, project_id, progress)
    except BaseException:
        analysis_id = progress.get("analysis_id")
        if analysis_id and not progress.get("finalized"):
            try:
                analyses.delete_analysis(project_id, analysis_id)
            except Exception:
                pass
        raise


async def _run_smart_merge(ctx: JobContext, project_id: str, progress: dict[str, Any]) -> dict[str, Any]:
    ctx.log("Starting Smart Merge...")
    ctx.progress(0.02)

    # 0. New analysis folder
    analysis_id, adir = analyses.create_analysis(project_id, "smart")
    progress["analysis_id"] = analysis_id
    ctx.log(f"Analysis folder: {analysis_id}")

    # 1. Load the data (reuse from merger.py)
    from services.merger import _load_inputs

    # Reading large consolidated workbooks can take minutes and there is no
    # other progress output in the meantime — so the user doesn't think it froze.
    ctx.log("Loading consolidated sources — large workbooks can take a few minutes...")
    try:
        scp_df, wos_df = await asyncio.to_thread(_load_inputs, project_id, ctx)
    except Exception:
        try:
            analyses.delete_analysis(project_id, analysis_id)
        except Exception:
            pass
        raise
    ctx.log(f"Data loaded: {len(wos_df)} WoS + {len(scp_df)} Scopus")
    ctx.progress(0.10)

    # Label the DB (_load_inputs returns a fresh frame — .copy() here only
    # created an extra in-memory copy of the corpus)
    wos_df["DB"] = "ISI"
    scp_df["DB"] = "SCOPUS"

    # NOTE: The CPU-heavy stages below (normalize / dedup / candidate generation /
    # matching / field merge) run in the run_cpu pool. Running them inline on the
    # event loop froze the loop for minutes on large corpora: SSE pings (15 s)
    # could not be sent, an idle monitoring connection dropped with
    # ERR_CONNECTION_RESET, and the rest of the application became unresponsive.

    # 2. Normalize — side columns
    ctx.log(f"Normalization: {len(wos_df) + len(scp_df)} rows")

    def _stage_normalize() -> None:
        # Columns are built FROM A LIST with dtype=object — NOT with `.apply()`.
        # In pandas 3, an apply() with text results yields a `str` dtype and
        # turns the None returns of the normalize functions (which mean "empty")
        # into NaN. Since NaN is truthy, every identifier check guarded by
        # `if not v` takes an empty DOI for a valid identifier: all DOI-less
        # records in the same source get collected under the single NaN key and
        # collapse to one (SILENT DATA LOSS), and DOI-less records can never
        # match anything. `astype(object)` does NOT FIX this — NaN stays NaN
        # inside an object dtype and is still truthy.
        def _norm_col(frame: pd.DataFrame, src: str, fn) -> pd.Series:
            raw = frame[src] if src in frame.columns else pd.Series([""] * len(frame), index=frame.index)
            return pd.Series([fn(v) for v in raw], index=frame.index, dtype=object)

        for df in (wos_df, scp_df):
            df["_norm_doi"] = _norm_col(df, "DI", normalize_doi)
            df["_norm_title"] = _norm_col(df, "TI", normalize_title)
            df["_norm_year"] = _norm_col(df, "PY", normalize_year)
            df["_norm_surname"] = _norm_col(df, "AU", normalize_author_surname)
            df["_norm_issn"] = _norm_col(df, "SN", normalize_issn)
            df["_norm_pmid"] = _norm_col(df, "PM", normalize_id_token)
            df["_norm_ut"] = _norm_col(df, "UT", normalize_id_token)
            df["_norm_journal"] = _norm_col(df, "SO", normalize_title)
            # Invariant — a loud error instead of silent loss: if the identifier
            # columns are not object dtype (if someone goes back to .apply() later),
            # empty identifiers become NaN again and the data loss silently returns.
            for _col in ("_norm_doi", "_norm_pmid", "_norm_ut"):
                if df[_col].dtype != object:
                    raise RuntimeError(
                        f"merge_invariant_dtype: {_col} dtype={df[_col].dtype} "
                        "(expected object; empty identifiers must stay None)"
                    )

    await run_cpu(_stage_normalize)
    ctx.progress(0.20)

    # 2b. WITHIN-source dedup — among rows with the same UT/EID or the same DOI,
    # the fullest one is kept (overlapping export files; cross-source matching
    # does not see these).
    wos_raw_n, scp_raw_n = len(wos_df), len(scp_df)
    (wos_df, intra_wos_removed), (scp_df, intra_scp_removed) = await run_cpu(
        lambda: (dedup_within_source(wos_df), dedup_within_source(scp_df))
    )
    if intra_wos_removed or intra_scp_removed:
        ctx.log(f"Intra-source dedup: {intra_wos_removed} WoS + {intra_scp_removed} Scopus duplicate rows removed")
    ctx.progress(0.25)

    # 3-4. Candidate generation (IDENTIFIER-FIRST: pairs with the same DOI/PMID
    # are evaluated regardless of blocking; blocking limits only the title
    # stages) + greedy one-to-one assignment.
    matches: list[dict] = []           # match_audit rows
    borderline: list[dict] = []        # borderline_queue rows
    matched_wos: set[int] = set()
    matched_scp: set[int] = set()

    # Counters
    stage_counts: dict[str, int] = {}
    pair_counter = 0

    candidates = await run_cpu(generate_candidates, wos_df, scp_df)
    ctx.log(f"Candidate pairs: {len(candidates)}")
    ctx.progress(0.40)

    # Greedy assignment + set aside the borderline pairs (in the CPU pool — the
    # number of candidates can reach hundreds of thousands on large corpora)
    def _stage_assign() -> int:
        pair_counter = 0
        for conf, w_idx, s_idx, m in candidates:
            if w_idx in matched_wos or s_idx in matched_scp:
                continue

            pair_counter += 1
            pair_id = f"p{pair_counter:06d}"

            if m["stage"] == "5_borderline":
                # Borderline — will wait for manual approval in the UI
                w_row = wos_df.loc[w_idx]
                s_row = scp_df.loc[s_idx]
                borderline.append({
                    "pair_id": pair_id,
                    "wos_index": int(w_idx),
                    "scp_index": int(s_idx),
                    "jw_title": m["jw_title"],
                    "year_diff": m["year_diff"],
                    "surname_match": m["surname_match"],
                    "doi_variant": bool(m.get("doi_variant", False)),
                    "confidence": m["confidence"],
                    "stage_label": m["stage_label"],
                    "reason": m["reason"],
                    "wos_doi": _to_str(w_row.get("DI", "")),
                    "scp_doi": _to_str(s_row.get("DI", "")),
                    # Identifiers: to find the rows of an accepted pair without
                    # relying on the DOI (for DOI-less pairs, acceptance silently
                    # did nothing). The Scopus EID is carried in the UT column.
                    "wos_ut": _to_str(w_row.get("UT", "")),
                    "scp_ut": _to_str(s_row.get("UT", "")),
                    "wos_title": _to_str(w_row.get("TI", ""))[:200],
                    "scp_title": _to_str(s_row.get("TI", ""))[:200],
                    "wos_year": w_row.get("_norm_year"),
                    "scp_year": s_row.get("_norm_year"),
                    "wos_surname": _to_str(w_row.get("_norm_surname", "")),
                    "scp_surname": _to_str(s_row.get("_norm_surname", "")),
                    "wos_journal": _to_str(w_row.get("SO", "")),
                    "scp_journal": _to_str(s_row.get("SO", "")),
                    "wos_volume": _cell_str(w_row.get("VL", "")),
                    "scp_volume": _cell_str(s_row.get("VL", "")),
                    "status": "pending",
                })
            else:
                # Definite match — merge
                matched_wos.add(w_idx)
                matched_scp.add(s_idx)
                stage_counts[m["stage_label"]] = stage_counts.get(m["stage_label"], 0) + 1
                matches.append({
                    "pair_id": pair_id,
                    "wos_index": int(w_idx),
                    "scp_index": int(s_idx),
                    "doi": m.get("doi", ""),
                    "stage": m["stage"],
                    "stage_label": m["stage_label"],
                    "confidence": m["confidence"],
                    "jw_title": m.get("jw_title"),
                    "year_diff": m.get("year_diff"),
                    "surname_match": m.get("surname_match"),
                    "reason": m["reason"],
                })
        return pair_counter

    pair_counter = await run_cpu(_stage_assign)
    ctx.log(f"Matches: {len(matches)} exact, {len(borderline)} borderline")
    for stage_label, n in stage_counts.items():
        ctx.log(f"  Stage [{stage_label}]: {n}")
    ctx.progress(0.65)

    # 5-7. Field merge + unmatched rows + single DataFrame + UID (in the CPU pool)
    ctx.log("Field merge (Caputo 2024 defaults)...")

    cr_stats: dict[str, int] = {"normalized": 0, "nr_filled": 0}

    def _stage_field_merge():
        all_columns = list(set(list(wos_df.columns) + list(scp_df.columns)))
        conflicts: list[dict] = []
        merged_rows: list[dict] = []
        field_source_distribution: dict[str, int] = {}

        for match in matches:
            w_idx = match["wos_index"]
            s_idx = match["scp_index"]
            w_row = wos_df.loc[w_idx].to_dict()
            s_row = scp_df.loc[s_idx].to_dict()
            merged_row, pair_conflicts = merge_pair_with_preferences(
                match["pair_id"], w_row, s_row, all_columns
            )
            merged_rows.append(merged_row)
            conflicts.extend(pair_conflicts)
            for c in pair_conflicts:
                src = c["chosen_source"]
                field_source_distribution[src] = field_source_distribution.get(src, 0) + 1

        # 6. Unmatched WoS / Scopus rows → appended to the main df as they are
        wos_not_matched = wos_df.loc[~wos_df.index.isin(matched_wos)].copy()
        scp_not_matched = scp_df.loc[~scp_df.index.isin(matched_scp)].copy()
        # Drop the _norm_* columns
        for df in (wos_not_matched, scp_not_matched):
            drop_cols = [c for c in df.columns if c.startswith("_norm_")]
            df.drop(columns=drop_cols, inplace=True, errors="ignore")

        merged_df = pd.DataFrame(merged_rows)
        # _norm_* drop
        drop_cols = [c for c in merged_df.columns if c.startswith("_norm_")]
        if drop_cols:
            merged_df.drop(columns=drop_cols, inplace=True, errors="ignore")

        # A single DataFrame + UID column (same schema as filter_engine)
        final_df = pd.concat([merged_df, wos_not_matched, scp_not_matched], ignore_index=True)
        filter_engine._ensure_uid_column(final_df)

        # COUNT PRESERVATION (lineage invariant): output = (source − intra-source
        # duplicates) − matched pairs. A deviation means silent record loss or
        # duplication; the pandas 3 NaN bug lost 70 publications in exactly this
        # way, without giving any warning. It is right to fail the run here
        # rather than write a corrupt dataset.
        _expected = ((wos_raw_n - intra_wos_removed)
                     + (scp_raw_n - intra_scp_removed)
                     - len(matches))
        if len(final_df) != _expected:
            raise RuntimeError(
                f"merge_lineage_mismatch: expected {_expected} records "
                f"(WoS {wos_raw_n}-{intra_wos_removed}, "
                f"Scopus {scp_raw_n}-{intra_scp_removed}, matched {len(matches)}) "
                f"but produced {len(final_df)}"
            )

        # CR normalization — citations in Scopus grammar are converted to WoS
        # grammar. Otherwise VOSviewer/bibliometrix cannot match any citation in
        # the Scopus-sourced rows of the merged dataset.
        # normalize_cr is idempotent; WoS rows come back as they are.
        if "CR" in final_df.columns:
            before = final_df["CR"].fillna("").astype(str)
            after = before.map(normalize_cr)
            # Only filled cells are written — empty/NaN CR cells are left as they are.
            filled_cr = before.str.strip() != ""
            changed = filled_cr & (after != before)
            cr_stats["normalized"] = int(changed.sum())
            if changed.any():
                final_df["CR"] = final_df["CR"].astype(object)
                final_df.loc[changed, "CR"] = after[changed]
            # NR (reference count): if blank OR if CR was rewritten in this step,
            # count from CR. Scopus files have no NR column; also, in matched rows
            # where CR came from Scopus via scopus_fallback, the WoS side's NR
            # (often 0) no longer describes the string.
            if "NR" not in final_df.columns:
                final_df["NR"] = ""
            nr = final_df["NR"]
            blank = nr.isna() | nr.astype(str).str.strip().isin(("", "nan", "NaN", "None"))
            fill = (blank | changed) & filled_cr
            if fill.any():
                # Convert to object: writing an integer into an int64/float64
                # column yields 3.0 instead of 3 and ends up in the WoS file as "NR 3.0".
                final_df["NR"] = final_df["NR"].astype(object)
                final_df.loc[fill, "NR"] = after[fill].map(count_refs)
                cr_stats["nr_filled"] = int(fill.sum())

        return final_df, conflicts, field_source_distribution, wos_not_matched, scp_not_matched

    (final_df, conflicts, field_source_distribution,
     wos_not_matched, scp_not_matched) = await run_cpu(_stage_field_merge)
    ctx.log(f"CR normalization (Scopus → WoS grammar): {cr_stats['normalized']} cells rewritten, "
            f"{cr_stats['nr_filled']} NR values filled")
    ctx.progress(0.80)

    # 8. Write the outputs — into the analysis folder
    ctx.log("Writing output files...")
    # Main dataset is parquet (internal working format); side review files stay xlsx.
    merged_dataset = adir / "merged.parquet"
    audit_xlsx = adir / "match_audit.xlsx"
    conflict_xlsx = adir / "conflict_log.xlsx"
    borderline_xlsx = adir / "borderline_queue.xlsx"
    lost_wos_xlsx = adir / "Lost_Wos_Records.xlsx"
    lost_scp_xlsx = adir / "Lost_Scopus_Records.xlsx"
    stat_xlsx = adir / "Statistic.xlsx"

    try:
        # Uncancellable: a cancel must not end the task (and release the exclusivity lock,
        # and let the cleanup below delete the folder) while a write thread is still running.
        _w = dataset_io.run_uncancellable
        await _w(dataset_io.atomic_write_dataset, final_df, merged_dataset)
        await _w(_write_match_audit, matches, audit_xlsx)
        await _w(_write_conflict_log, conflicts, conflict_xlsx)
        await _w(_write_borderline_queue, borderline, borderline_xlsx)
        await _w(_write_lost_records, wos_not_matched, lost_wos_xlsx)
        await _w(_write_lost_records, scp_not_matched, lost_scp_xlsx)
        await _w(
            _write_statistic_smart,
            len(final_df), wos_raw_n, scp_raw_n, final_df, stat_xlsx,
        )
    except Exception:
        # Error while writing — clean up the half-written analysis folder
        try:
            analyses.delete_analysis(project_id, analysis_id)
        except Exception:
            pass
        raise

    # 9. Save the borderline state (inside the analysis folder)
    state = {b["pair_id"]: {"status": "pending", "decided_at": None} for b in borderline}
    if borderline:
        _write_borderline_state(project_id, state, adir=adir)

    # 10. Invalidate the filter cache (Smart is about to become active)
    filter_engine._DF_CACHE.clear()

    # 11. Finalize — file_count + make active
    analyses.finalize_analysis(project_id, analysis_id)
    progress["finalized"] = True

    ctx.progress(1.0)
    storage.touch_project(project_id)

    # 12. Result summary (for the audit hook)
    summary = {
        "method": "smart",
        "analysis_id": analysis_id,
        "scopus_input": int(scp_raw_n),
        "wos_input": int(wos_raw_n),
        "merged_count": int(len(final_df)),
        "matched_pairs": int(len(matches)),
        "intra_wos_removed": int(intra_wos_removed),
        "intra_scopus_removed": int(intra_scp_removed),
        "borderline_count": int(len(borderline)),
        "borderline_pending": int(len(borderline)),
        "conflict_count": int(len(conflicts)),
        "match_stages": stage_counts,
        "field_source_distribution": field_source_distribution,
        "lost_wos_count": int(len(wos_not_matched)),
        "lost_scopus_count": int(len(scp_not_matched)),
        "output_dataset": str(merged_dataset.relative_to(storage.settings.storage_path)),
        "output_files": [
            f.name for f in (
                merged_dataset, audit_xlsx, conflict_xlsx, borderline_xlsx,
                lost_wos_xlsx, lost_scp_xlsx, stat_xlsx,
            ) if f.exists()
        ],
    }
    ctx.log(f"Smart Merge complete — {summary['merged_count']} unique records "
            f"({summary['matched_pairs']} matched + {summary['borderline_count']} borderline)")
    return summary


# ════════════════════════════════════════════════════════════════════════
#  PHASE 6 — Borderline review
# ════════════════════════════════════════════════════════════════════════

def _cell_str(v: Any) -> str:
    """Cell -> str; blank cells read back from xlsx as NaN become "" (never "nan").

    Whole-number floats (a numeric VL/IS column, or Excel's number cells) are written
    without the ".0" ("15", not "15.0").
    """
    try:
        if v is None or pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    text = str(v)
    # Queues written by older runs stored numeric volumes as "15.0".
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def _opt_int(v: Any) -> Optional[int]:
    """Cell -> int, or None for blanks/NaN (queue rows written by older runs)."""
    try:
        if v is None or pd.isna(v):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _opt_bool(v: Any) -> Optional[bool]:
    """Cell -> bool, or None when the cell is blank/NaN."""
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _row_doi_variant(row: Any) -> bool:
    """True when the pair's DOIs differ only by spelling.

    New queues carry an explicit ``doi_variant`` column; queues written by older
    runs are detected from the stored reason text (current English note or the
    legacy one).
    """
    flag = _opt_bool(row.get("doi_variant"))
    if flag is not None:
        return flag
    reason = str(row.get("reason", ""))
    return "DOI spelling variant" in reason or "DOI yaz" in reason


def list_borderline(project_id: str) -> list[dict]:
    """Combined list of borderline_queue.xlsx + borderline_state.json (active analysis)."""
    adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        return []
    bq_path = adir / "borderline_queue.xlsx"
    if not bq_path.exists():
        return []
    try:
        df = pd.read_excel(bq_path)
    except Exception:
        return []
    state = _read_borderline_state(project_id, adir=adir)
    items: list[dict] = []
    for _, row in df.iterrows():
        pair_id = _cell_str(row.get("pair_id"))
        # DOI is decisive: if the sanitized DOIs differ, it is never the same
        # publication. Even if such pairs are left in old (pre-fix) queues, do NOT
        # show them for manual approval — the user is asked only about genuinely
        # ambiguous pairs.
        if doi_conflict(row.get("wos_doi"), row.get("scp_doi")):
            continue
        st = state.get(pair_id, {"status": "pending"})
        items.append({
            "pair_id": pair_id,
            "wos_index": int(row.get("wos_index", 0)),
            "scp_index": int(row.get("scp_index", 0)),
            "jw_title": float(row.get("jw_title") or 0),
            "confidence": float(row.get("confidence") or 0),
            "status": st.get("status", "pending"),
            "decided_at": st.get("decided_at"),
            "reason": _cell_str(row.get("reason")),
            "year_diff": _opt_int(row.get("year_diff")),
            "surname_match": _opt_bool(row.get("surname_match")),
            "doi_variant": _row_doi_variant(row),
            "wos_doi": _cell_str(row.get("wos_doi")) or None,
            "scp_doi": _cell_str(row.get("scp_doi")) or None,
            "wos": {
                "doi": _cell_str(row.get("wos_doi")) or None,
                "title": _cell_str(row.get("wos_title")),
                "year": int(row["wos_year"]) if pd.notna(row.get("wos_year")) else None,
                "surname": _cell_str(row.get("wos_surname")) or None,
                "journal": _cell_str(row.get("wos_journal")) or None,
                "volume": _cell_str(row.get("wos_volume")) or None,
            },
            "scopus": {
                "doi": _cell_str(row.get("scp_doi")) or None,
                "title": _cell_str(row.get("scp_title")),
                "year": int(row["scp_year"]) if pd.notna(row.get("scp_year")) else None,
                "surname": _cell_str(row.get("scp_surname")) or None,
                "journal": _cell_str(row.get("scp_journal")) or None,
                "volume": _cell_str(row.get("scp_volume")) or None,
            },
            "llm_suggestion": st.get("llm_suggestion"),
        })
    return items


def _merge_accepted_pairs(
    df: pd.DataFrame, accept_pairs: list[dict], already: set[str],
) -> tuple[pd.DataFrame, dict[str, str], list[dict]]:
    """Actually MERGE the accepted borderline pairs (not delete).

    The old behavior only deleted the Scopus row; as a result, for every
    accepted pair the Scopus-preferred fields (AB, AU, AF, C1), the DE/ID union
    and the cross-fills were lost, and no conflict record was ever written.
    Now the merge stage's own rules (Caputo preferences) are applied: an
    accepted pair produces whatever it would have produced had it been counted
    as a definite match during the merge.

    Rows are located by IDENTIFIER (WoS UT / Scopus EID), falling back to the
    normalized DOI, and finally to the normalized title+year; at each step the
    DB column verifies the side. An ambiguous (multiple hits) or missing row is
    never silently assumed: the pair returns as "unresolved" and waits in the
    queue.

    Returns: (new_df, {pair_id: outcome}, conflict_records)
    """
    cols = [c for c in df.columns if c != "UID" and not c.startswith("_norm_")]
    ut_series = df["UT"].map(_to_str).str.lower() if "UT" in df.columns else None
    doi_series = df["DI"].map(normalize_doi) if "DI" in df.columns else None
    title_series = df["TI"].map(normalize_title) if "TI" in df.columns else None
    year_series = df["PY"].map(normalize_year) if "PY" in df.columns else None
    db_series = df["DB"].map(_to_str).str.upper() if "DB" in df.columns else None

    def _side_mask(scopus_side: bool):
        if db_series is None:
            return pd.Series(True, index=df.index)
        return (db_series == "SCOPUS") if scopus_side else (db_series != "SCOPUS")

    def _locate(ut: Any, doi: Any, title: Any, year: Any, scopus_side: bool) -> list:
        side = _side_mask(scopus_side)
        ut_v = _to_str(ut)
        if _present(ut_v) and ut_series is not None:
            hit = list(df.index[(ut_series == ut_v.lower()) & side])
            if hit:
                return hit
        doi_v = normalize_doi(doi)
        if _present(doi_v) and doi_series is not None:
            hit = list(df.index[(doi_series == doi_v) & side])
            if hit:
                return hit
        t = normalize_title(title)
        if t and title_series is not None:
            mask = (title_series == t) & side
            y = normalize_year(year)
            if y is not None and year_series is not None:
                mask = mask & (year_series == y)
            return list(df.index[mask])
        return []

    outcomes: dict[str, str] = {}
    conflicts: list[dict] = []
    used: set = set()
    drop: set = set()

    def _conf(pair: dict) -> float:
        try:
            return float(pair.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # Most reliable pair first: if two acceptances are tied to the same row, the
    # result should not change depending on the decision order.
    for pair in sorted(accept_pairs, key=lambda p: (-_conf(p), str(p.get("pair_id", "")))):
        pid = str(pair.get("pair_id", ""))
        if pid in already:
            outcomes[pid] = "unresolved:already_accepted"
            continue

        w_hits = [i for i in _locate(pair.get("wos_ut"), pair.get("wos_doi"),
                                     pair.get("wos_title"), pair.get("wos_year"), False)]
        s_hits = [i for i in _locate(pair.get("scp_ut"), pair.get("scp_doi"),
                                     pair.get("scp_title"), pair.get("scp_year"), True)]
        if len(w_hits) > 1 or len(s_hits) > 1:
            outcomes[pid] = "unresolved:ambiguous"
            continue
        w_idx = w_hits[0] if w_hits else None
        s_idx = s_hits[0] if s_hits else None

        if w_idx is None and s_idx is None:
            outcomes[pid] = "unresolved:both_missing"
            continue
        if w_idx is None:
            # The Scopus row is still there but the WoS side is missing (the user
            # may have deleted it) — a row left on its own is NOT DELETED, so that
            # no data is lost.
            outcomes[pid] = "unresolved:wos_missing"
            continue
        if s_idx is None:
            # The Scopus row is already gone: the decision is effectively applied.
            outcomes[pid] = "noop_scp_missing"
            continue
        if w_idx in used or s_idx in used or w_idx in drop or s_idx in drop:
            outcomes[pid] = "unresolved:row_already_merged"
            continue

        w_row = df.loc[w_idx].to_dict()
        s_row = df.loc[s_idx].to_dict()
        merged, pair_conflicts = merge_pair_with_preferences(pid, w_row, s_row, cols)
        if "DB_Original" in merged and "DB_Original" not in df.columns:
            df["DB_Original"] = ""
        for col, val in merged.items():
            if col not in df.columns or col == "UID":
                continue
            if val == "" and df[col].dtype != object:
                continue  # do not write an empty string into a numeric column
            df.at[w_idx, col] = val
        # If the merged CR's reference count changed, fix NR.
        if "CR" in merged and "NR" in df.columns:
            new_cr = _to_str(merged.get("CR"))
            if new_cr and new_cr != _to_str(w_row.get("CR")):
                df["NR"] = df["NR"].astype(object)
                df.at[w_idx, "NR"] = count_refs(new_cr)

        conflicts.extend(c for c in pair_conflicts if c.get("field") not in ("UID", "NR"))
        used.add(w_idx)
        drop.add(s_idx)
        outcomes[pid] = "merged"

    if drop:
        df = df.drop(index=list(drop)).reset_index(drop=True)
    return df, outcomes, conflicts


def decide_borderline(project_id: str, decisions: list[dict]) -> dict[str, Any]:
    """Apply the user's borderline decisions (on the active analysis).

    decisions: [{pair_id, decision: 'accept'|'reject'|'skip'}]
    """
    if not decisions:
        return {"applied": 0, "snapshot": None, "pending_after": 0}

    adir = analyses.get_active_analysis_dir(project_id)
    if adir is None:
        raise RuntimeError("no_active_analysis")

    # Get the dataset path from the resolver — the extension (parquet/legacy xlsx)
    # is not assumed here; a legacy project may have been migrated to parquet on
    # first touch.
    merged_path = analyses.active_dataset_path(project_id)
    if merged_path is None or not merged_path.exists():
        raise RuntimeError("no_merged_data")

    bq_path = adir / "borderline_queue.xlsx"
    if not bq_path.exists():
        return {"applied": 0, "snapshot": None, "pending_after": 0}

    # Update the state
    state = _read_borderline_state(project_id, adir=adir)
    # Pairs ALREADY accepted BEFORE this call — if accepted again, they are not
    # merged a second time (idempotent).
    prior_accepted = {pid for pid, v in state.items() if v.get("status") == "accept"}
    # State as it was BEFORE this call: stored next to the pre-accept snapshot so that a
    # snapshot restore can roll the accept decisions back together with the dataset.
    state_before = copy.deepcopy(state)
    bq_df = pd.read_excel(bq_path)
    bq_by_id = {str(r["pair_id"]): r for _, r in bq_df.iterrows()}

    accept_pairs: list[dict] = []
    now_ts = time.time()
    for d in decisions:
        pair_id = d.get("pair_id")
        decision = d.get("decision")
        if not pair_id or decision not in ("accept", "reject", "skip"):
            continue
        # DOI is decisive: a pair whose sanitized DOIs differ is NEVER the same
        # publication. Even if it came from an old (pre-fix) queue, no decision is
        # applied — the rule is authoritative in the apply layer just as it is in
        # the display layer (list_borderline); a direct API call cannot cause a
        # wrong merge.
        bq_row = bq_by_id.get(pair_id)
        if bq_row is not None and doi_conflict(bq_row.get("wos_doi"), bq_row.get("scp_doi")):
            continue
        state[pair_id] = {
            **state.get(pair_id, {}),
            "status": decision,
            "decided_at": now_ts,
        }
        if decision == "accept" and pair_id in bq_by_id:
            # "accept" is NOT written to the state IMMEDIATELY; it is written after
            # its counterpart is found in the dataset and actually applied (below).
            state.pop(pair_id, None)
            state[pair_id] = {**state.get(pair_id, {}), "status": "pending"}
            accept_pairs.append(bq_by_id[pair_id])

    _write_borderline_state(project_id, state, adir=adir)

    # Dataset update for the accepted pairs — ACTUAL MERGE.
    snapshot_rel: Optional[str] = None
    applied = 0
    merged_pairs = 0
    conflicts_logged = 0
    resolved_ids: list[str] = []
    unresolved_ids: list[str] = []
    unresolved_reasons: dict[str, str] = {}
    if accept_pairs:
        df_before = dataset_io.read_dataset(merged_path)
        df_after, outcomes, pair_conflicts = _merge_accepted_pairs(
            df_before.copy(), accept_pairs, prior_accepted,
        )
        for pid, outcome in outcomes.items():
            if outcome in ("merged", "noop_scp_missing", "unresolved:already_accepted"):
                resolved_ids.append(pid)
            else:
                unresolved_ids.append(pid)
                unresolved_reasons[pid] = outcome.split(":", 1)[-1]
        merged_pairs = sum(1 for o in outcomes.values() if o == "merged")

        if merged_pairs:
            # Invariants — on violation the dataset is NOT TOUCHED.
            if len(df_after) != len(df_before) - merged_pairs:
                raise RuntimeError(
                    f"borderline_merge_invariant: expected {len(df_before)} - {merged_pairs} "
                    f"rows but got {len(df_after)}"
                )
            if "UID" in df_after.columns and not df_after["UID"].astype(str).is_unique:
                raise RuntimeError("borderline_uid_not_unique")

            # Snapshot of the PREVIOUS dataset — only if something will really change.
            snaps_dir = analyses.work_dir(project_id) / "snapshots"
            snaps_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
            snap_path = snaps_dir / f"pre_borderline_accept_{stamp}.parquet"
            dataset_io.atomic_write_dataset(df_before, snap_path)
            snapshot_rel = str(snap_path.relative_to(storage.settings.storage_path))
            try:
                _borderline_state_copy_path(snap_path).write_text(
                    json.dumps(state_before, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass  # best effort: without the copy a restore leaves the state untouched

            dataset_io.atomic_write_dataset(df_after, merged_path)
            filter_engine._DF_CACHE.clear()
            applied = merged_pairs

            # Side artifacts — best effort; a failure does not roll back the decision.
            try:
                conflicts_logged = _append_conflict_log(adir / "conflict_log.xlsx", pair_conflicts)
            except Exception:
                conflicts_logged = 0
            try:
                _refresh_statistic_after_borderline(adir, df_after)
            except Exception:
                pass

    # Applied acceptances are now written to the state; the ones that could not
    # be applied stay "pending" (they keep appearing in the queue).
    if accept_pairs:
        for pid in resolved_ids:
            state[pid] = {**state.get(pid, {}), "status": "accept", "decided_at": now_ts}
        for pid in unresolved_ids:
            state[pid] = {**state.get(pid, {}), "status": "pending", "unresolved_at": now_ts,
                          "unresolved_reason": unresolved_reasons.get(pid, "")}
    _write_borderline_state(project_id, state, adir=adir)

    # Audit
    _bl_accept = len([d for d in decisions if d.get("decision") == "accept"])
    _bl_reject = len([d for d in decisions if d.get("decision") == "reject"])
    _bl_skip = len([d for d in decisions if d.get("decision") == "skip"])
    audit.write(
        project_id,
        kind="merge_borderline",
        title=f"Borderline: {_bl_accept} accepted / {_bl_reject} rejected / {_bl_skip} skipped",
        title_key="audit.titles.borderlineDecisions",
        title_params={"accept": _bl_accept, "reject": _bl_reject, "skip": _bl_skip},
        details={
            "decisions_count": len(decisions),
            "applied_changes": applied,
            "accept_pair_ids": [d.get("pair_id") for d in decisions if d.get("decision") == "accept"][:50],
            "unresolved_pair_ids": unresolved_ids[:50],
            "unresolved_reasons": {k: unresolved_reasons[k] for k in unresolved_ids[:50] if k in unresolved_reasons},
            "merged_pairs": merged_pairs,
            "conflicts_logged": conflicts_logged,
        },
        snapshot=snapshot_rel,
        user_action="borderline_decide",
    )

    pending_after = sum(1 for v in state.values() if v.get("status") == "pending")
    return {
        "applied": applied,
        "snapshot": snapshot_rel,
        "pending_after": pending_after,
        # Pairs that were accepted but whose counterpart could not be found in the
        # dataset — the UI must not show them as "applied".
        "unresolved": unresolved_ids,
        "unresolved_reasons": unresolved_reasons,
        "merged_pairs": merged_pairs,
    }
