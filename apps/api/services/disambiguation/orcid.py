"""ORCID-first helpers for author name disambiguation.

Resolves author identity for an ambiguous group using ORCID identifiers,
preferring values already present in the data (OI column) and falling back to
live OpenAlex lookups by DOI. Matching ORCIDs imply the same person; disjoint
ORCIDs imply different people.

Provides utilities to fetch ORCIDs and affiliations for a DOI, parse OI cells,
collect a candidate's ORCID set, backfill empty rows from DOI metadata, and
classify a group as merge/conflict/unknown.
"""

from __future__ import annotations

from functools import lru_cache

from bibex_core.modules.api_utils import _get_with_retry
from .similarity import normalize_name
from .blocking import _SPLIT_SUFFIX_RE


def _clean_orcid(v) -> str:
    """https://orcid.org/0000-... → 0000-... (bare ORCID)."""
    return (
        str(v or "")
        .replace("https://orcid.org/", "")
        .replace("http://orcid.org/", "")
        .strip()
    )


def _norm(name: str) -> str:
    """Name-matching key: split suffix ('(b)') stripped + normalized."""
    return normalize_name(_SPLIT_SUFFIX_RE.sub("", str(name or "")))


@lru_cache(maxsize=8192)
def fetch_orcids_for_doi(doi: str, email: str | None = None) -> tuple:
    """Fetch ONLY the author ORCID/ROR/country for a DOI from OpenAlex (lightweight: select=authorships).

    Returns: a tuple of ((display_name, orcid, ror, cc), ...) (so it is hashable for lru_cache).
    Error/empty → empty tuple. Does not fetch full metadata (~95% smaller payload).
    """
    d = str(doi or "").strip()
    if not d:
        return tuple()
    url = f"https://api.openalex.org/works/doi:{d}?select=authorships,doi"
    if email:
        url += f"&mailto={email}"
    try:
        r = _get_with_retry(url)
    except Exception:
        return tuple()
    if getattr(r, "status_code", None) != 200:
        return tuple()
    try:
        work = r.json()
    except Exception:
        return tuple()

    out: list[tuple] = []
    for a in (work.get("authorships") or []):
        au = a.get("author") or {}
        name = au.get("display_name") or ""
        orcid = _clean_orcid(au.get("orcid"))
        rors, ccs = [], []
        for inst in (a.get("institutions") or []):
            if inst.get("ror"):
                rors.append(str(inst["ror"]).replace("https://ror.org/", "").strip())
            if inst.get("country_code"):
                ccs.append(str(inst["country_code"]).strip().upper())
        for cc in (a.get("countries") or []):
            if cc:
                ccs.append(str(cc).strip().upper())
        out.append((name, orcid, "; ".join(dict.fromkeys(rors)), "; ".join(dict.fromkeys(ccs))))
    return tuple(out)


@lru_cache(maxsize=8192)
def fetch_affiliations_for_doi(doi: str, email: str | None = None) -> tuple:
    """Fetch (institution_name, canonical_country) pairs for a DOI from OpenAlex (address completion).

    Returns: ((institution_display_name, country_name), ...) — country_name is the canonical
    English name (converted from country_code; '' if unknown). Error/empty → empty tuple.
    Lightweight: select=authorships. A DOI is not fetched again thanks to lru_cache.
    """
    from bibex_core.modules.c1_utils import country_from_code

    d = str(doi or "").strip()
    if not d:
        return tuple()
    url = f"https://api.openalex.org/works/doi:{d}?select=authorships,doi"
    if email:
        url += f"&mailto={email}"
    try:
        r = _get_with_retry(url)
    except Exception:
        return tuple()
    if getattr(r, "status_code", None) != 200:
        return tuple()
    try:
        work = r.json()
    except Exception:
        return tuple()

    out: list[tuple] = []
    seen: set = set()
    for a in (work.get("authorships") or []):
        for inst in (a.get("institutions") or []):
            name = str(inst.get("display_name") or "").strip()
            country = country_from_code(inst.get("country_code") or "") or ""
            key = (name.lower(), country)
            if (name or country) and key not in seen:
                seen.add(key)
                out.append((name, country))
    return tuple(out)


def parse_oi_cell(cell) -> dict[str, str]:
    """Turn a WoS OI cell ('Name/0000-...; Name2/0000-...') into {normalize_name(name): orcid}.

    Tolerates both the '/' (WoS) and ', ' variants; skips the entry if there is no name.
    """
    s = str(cell or "").strip()
    if not s or s.lower() in ("nan", "none"):
        return {}
    out: dict[str, str] = {}
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        # 'Name/ORCID' form
        if "/" in part:
            name, _, oid = part.rpartition("/")
        else:
            # may be an ORCID only — cannot be tied to a name, skip
            continue
        oid = _clean_orcid(oid)
        nm = _norm(name)
        if nm and oid:
            out[nm] = oid
    return out


def orcids_for_candidate(df, candidate: dict, *, email: str | None, allow_api: bool) -> set[str]:
    """ORCID set of a candidate.

    Using the candidate['records'] row indices + candidate['name_variants'] (or
    'variants'): collects the ORCIDs of the authors that MATCH the candidate's
    name variants, FIRST from each row's OI cell and, if missing (when allow_api
    is set), from an OpenAlex query by DOI. A name match is required — otherwise
    a co-author's ORCID would not be picked up.

    NO side effects (does not write to the data); read-only. (Write-back is done separately.)
    """
    variants = candidate.get("name_variants") or candidate.get("variants") or []
    want = {_norm(v) for v in variants if v}
    if candidate.get("id"):
        want.add(_norm(candidate["id"]))
    want.discard("")
    if not want:
        return set()

    records = candidate.get("records") or []
    has_oi = "OI" in df.columns
    has_di = "DI" in df.columns
    found: set[str] = set()

    for idx in records:
        if idx not in df.index:
            continue
        # 1) OI already present in the data
        if has_oi:
            for nm, oid in parse_oi_cell(df.at[idx, "OI"]).items():
                if nm in want:
                    found.add(oid)
        # 2) If missing, on the fly from the DOI (only for the name matched in this row)
        if allow_api and has_di:
            doi = df.at[idx, "DI"]
            if doi is not None and str(doi).strip() and str(doi).strip().lower() != "nan":
                for name, orcid, _ror, _cc in fetch_orcids_for_doi(str(doi).strip(), email):
                    if orcid and _norm(name) in want:
                        found.add(orcid)
    return found


def _is_blank(v) -> bool:
    s = "" if v is None else str(v).strip()
    return s == "" or s.lower() in ("nan", "none")


def enrich_rows_from_doi(df, row_indices, *, email: str | None) -> dict:
    """Fill the EMPTY OI/RI/ROR/CC cells of the given rows via OpenAlex, by DOI.

    Writes only into empty cells (does not overwrite an existing/original value). df is updated IN PLACE.
    Returns: {'rows_filled': int, 'dois_fetched': int}. Side effect: df mutation.
    """
    if "DI" not in df.columns:
        return {"rows_filled": 0, "dois_fetched": 0}
    # Create the new columns if they don't exist (object dtype) — for writing into empty cells
    for col in ("OI", "RI", "ROR", "CC"):
        if col not in df.columns:
            df[col] = None

    rows_filled = 0
    dois_fetched = 0
    seen_doi: set[str] = set()
    for idx in dict.fromkeys(row_indices):  # deduplicate, preserve order
        if idx not in df.index:
            continue
        doi = df.at[idx, "DI"]
        if doi is None or not str(doi).strip() or str(doi).strip().lower() == "nan":
            continue
        d = str(doi).strip()
        rows = fetch_orcids_for_doi(d, email)  # lru_cache-backed
        if d not in seen_doi:
            seen_doi.add(d)
            if rows:
                dois_fetched += 1
        if not rows:
            continue
        # Row-level combined strings (WoS format)
        ois = [f"{n}/{o}" for (n, o, _r, _c) in rows if n and o]
        ris = [f"{n}/{o}" for (n, o, _r, _c) in rows if n]  # no RI; name/orcid fallback in place of name/id
        rors, ccs = [], []
        for (_n, _o, r, c) in rows:
            if r:
                rors += [t.strip() for t in r.split(";") if t.strip()]
            if c:
                ccs += [t.strip() for t in c.split(";") if t.strip()]
        updates = {
            "OI": "; ".join(dict.fromkeys(ois)),
            "ROR": "; ".join(dict.fromkeys(rors)),
            "CC": "; ".join(dict.fromkeys(ccs)),
        }
        changed = False
        for col, val in updates.items():
            if val and _is_blank(df.at[idx, col]):
                df.at[idx, col] = val
                changed = True
        if changed:
            rows_filled += 1
    return {"rows_filled": rows_filled, "dois_fetched": dois_fetched}


def classify_by_orcid(orcid_sets: list[set[str]]) -> str:
    """Group decision from the per-member ORCID sets.

      'merge'    — members with a known ORCID converge on a SINGLE shared ORCID → same person
      'conflict' — two members carry disjoint (non-intersecting) ORCIDs → different people
      'unknown'  — insufficient ORCID evidence → leave to the name+field heuristic / LLM
    """
    known = [s for s in orcid_sets if s]
    if len(known) < 2:
        return "unknown"
    # If any two known sets do not intersect → conflict (different people)
    for i in range(len(known)):
        for j in range(i + 1, len(known)):
            if not (known[i] & known[j]):
                return "conflict"
    # All known sets intersect pairwise → shared ORCID(s) exist → same person
    return "merge"
