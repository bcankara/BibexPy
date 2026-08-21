"""Cited-reference (CR) normalization — Scopus grammar rewritten as WoS grammar.

Why this module exists
----------------------
Downstream consumers of a WoS plain-text file (VOSviewer, bibliometrix'
``convert2df(dbsource="isi")``, CitNetExplorer, ...) parse the ``CR`` field with
the *Web of Science* reference grammar::

    FIRSTAUTHOR IN, YYYY, SOURCE, Vnn, Pnn, DOI xx

Everything after author+year is optional and individual references are joined
with ``"; "``.  Scopus, however, exports two entirely different grammars:

* **new format** — ``Author1 A.; Author2 B., Title, Journal, Vol, Issue, pp. X-Y, (YYYY)``
  Authors are separated by ``;`` *inside* one reference and references are
  separated by ``;`` too, so a naive ``split(";")`` shreds every reference into
  author fragments.  The only reliable boundary is a ``;`` that directly follows
  a trailing ``(yyyy)`` — the same rule bibliometrix uses in
  ``fix_scopus_author_separator``.
* **classic format** — ``Author1, Author2, ..., Title (YYYY) Journal, Vol, Pages``
  The year sits mid-string and authors are comma-separated, so ``;`` is an
  unambiguous reference separator here.

Feeding either Scopus grammar into a WoS file produces references that no
consumer can key on.  VOSviewer (Manual 1.6.20) matches cited references by
(1) first author + year + volume + begin page, (2) first author + year +
first three alphanumerics of the source + begin page when there is no volume,
(3) DOI, and only as a last resort (4) the raw string for bibliographic
coupling.  Extracting author/year/volume/page is therefore what decides
reference-level analysis quality; DOI is effectively absent from real Scopus CR
data (0 of 300 sampled references carried one).

Design rules
------------
* ``normalize_cr`` is **idempotent** — WoS-shaped input is returned unchanged
  (byte-identical) and re-normalizing an already-normalized cell is a no-op.
* Handling is **per reference**, not per cell: a merged corpus routinely holds
  cells that mix WoS and Scopus references.
* References that cannot be converted (no year, bare fragments, books, URLs)
  are **never dropped**.  They are kept with their internal ``;`` replaced by
  ``,`` — bibliometrix' neutralization trick — which keeps the WoS writer's
  ``;`` split safe and still allows VOSviewer's rule-4 exact string matching.
"""

from __future__ import annotations

import re

__all__ = ["normalize_cr", "count_refs"]


# ── Building blocks ──────────────────────────────────────────────────────

# Plausible publication years only.  Using \d{4} here would let article
# numbers, DOI fragments such as "10.1061/(ASCE)...(2004)130:6(646)" and page
# ranges masquerade as years.
_YEAR = r"(?:1[5-9]|20)\d{2}"

# Reference boundary inside a Scopus "new format" cell: a ";" that immediately
# follows a trailing "(yyyy)".  Kept as bibliometrix writes it (plain \d{4}) so
# that splitting behaviour matches the reference implementation exactly.
_NEW_BOUNDARY = re.compile(r"(?<=\(\d{4}\))\s*;\s*")

# Scopus new format: the reference ends with "(yyyy)".
_TRAILING_YEAR = re.compile(r"\((" + _YEAR + r")\)\s*$")

# Scopus classic format: "(yyyy) " followed by the source name (mid-string).
_CLASSIC_YEAR = re.compile(r"\((" + _YEAR + r")\)\s+(?=[^\s\d])")

# WoS format: "AUTHOR, YYYY, ..." or the author-less "YYYY, ..." variant that
# WoS itself emits for anonymous records.  A trailing ";" is tolerated so that a
# WoS reference followed by more references in the same chunk is still detected.
# Clarivate ships TWO author spellings depending on export vintage/route:
# "HESKETT JL, 1994, ..." (comma-less) and "HESKETT, JL, 1994, ..." (a comma
# between surname and initials) — both are recognised as WoS grammar here.
_WOS_REF = re.compile(
    r"^\s*(?:[^,;]{1,150},\s*(?:[A-Z]{1,3},\s*)?" + _YEAR + r"|" + _YEAR + r")\s*(?:[,;]|$)"
)

# The comma variant defeats VOSviewer's structural parser (the extra comma
# shifts the year out of its expected slot, so the reference falls back to
# raw-string comparison and never cross-matches the comma-less form our Scopus
# conversion emits).  Strip exactly the "<surname>, <1-3 CAPS>, <year>," shape:
# institutional authors ("OECD, 2019, ..." — year right after the comma) and
# "[Anonymous], 2013, ..." are untouched.  Verified against a raw Clarivate
# export where 67.6% of 35,952 references carried the comma form.
_WOS_AUTHOR_COMMA = re.compile(
    r"(^|(?<=;)\s*)([^,;]+), ([A-Z]{1,3}), (?=(?:1[5-9]|20)\d{2},)"
)


def _strip_wos_author_comma(cell: str) -> str:
    """"SURNAME, II, YYYY, ..." → "SURNAME II, YYYY, ..." (idempotent)."""
    return _WOS_AUTHOR_COMMA.sub(r"\1\2 \3, ", cell)

# A DOI token; stops at whitespace, comma, semicolon or a closing bracket.
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s,;\]]+)")

# "pp. 123-145", "pp. 123–145" (en dash), "p. 5" and bare "pp. 5" all yield the
# begin page, which is the only page VOSviewer keys on.
_PAGES = re.compile(r"\bpp?\.\s*(\d+)", re.IGNORECASE)
_PAGES_TOKEN = re.compile(r"^pp?\.\s*\d", re.IGNORECASE)

# Any parenthesised year — removed from an extracted source so a converted
# reference can never end with "(yyyy)" and be re-parsed as Scopus input.
_PAREN_YEAR = re.compile(r"\(\s*" + _YEAR + r"\s*\)")

_WS = re.compile(r"\s+")
_MULTI_COMMA = re.compile(r"(?:\s*,\s*){2,}")
_URLISH = re.compile(r"^(?:https?://|www\.|doi\b|10\.\d{4,9}/)", re.IGNORECASE)

# "730-732", "4-90", "15-23" (any Unicode dash) — a bare number range is a page
# or volume range, never a source name.
_NUMBER_RANGE = re.compile(r"^[\d\s./\-‐-―]+$")

# A source longer than this is almost certainly a chained book/report blob
# rather than a journal or proceedings title; better no source than a wrong one.
_MAX_SOURCE_LEN = 200


def _squeeze(text: str) -> str:
    """Collapse all whitespace runs to a single space and strip the ends."""
    return _WS.sub(" ", text).strip()


def _first_author(author_block: str) -> str:
    """Return the first author of a Scopus author block in WoS shape.

    ``"Anderson E.W.; Fornell C."`` → ``"Anderson EW"``.  Initial dots are
    dropped and initials that were separated by a space or a hyphen are fused,
    which is how WoS writes them ("HAUGSTVEDT AC", not "HAUGSTVEDT A.-C.").
    Multi-letter hyphenated surnames ("FRIDOVICH-KEIL") are left alone.
    """
    first = re.split(r"[;,]", author_block, maxsplit=1)[0]
    # Drop the dot after an initial ("E.W." → "EW") but keep abbreviations such
    # as "St. John" readable by turning their dots into spaces instead.
    first = re.sub(r"(?<=\b[A-Za-z])\.", "", first).replace(".", " ")
    first = _squeeze(first)
    # Fuse "A C" / "A-C" style initials, repeatedly for three or more initials.
    for _ in range(3):
        fused = re.sub(r"(?<=\b[A-Za-z])[\s-]+(?=[A-Za-z]\b)", "", first)
        if fused == first:
            break
        first = fused
    return _squeeze(first)


def _pick_volume(tokens: list[str], year: str, start: int = 1) -> int | None:
    """Index of the first plain-number comma token — the volume candidate.

    Tokens longer than four digits are article numbers, not volumes, and page
    tokens never survive as bare numbers because Scopus prefixes them "pp.".
    A token equal to the reference year is a stray year, not a volume.
    """
    for i in range(start, len(tokens)):
        tok = tokens[i]
        if tok.isdigit() and 1 <= len(tok) <= 4 and tok != year:
            return i
    return None


def _clean_source(token: str) -> str:
    """Uppercase a source candidate, or return "" when it is not credible."""
    src = _PAREN_YEAR.sub(" ", token)
    src = _squeeze(src).strip(" .,;:-")
    if not src or len(src) > _MAX_SOURCE_LEN:
        return ""
    if _NUMBER_RANGE.match(src) or _PAGES_TOKEN.match(src) or _URLISH.match(src):
        return ""
    return src.upper()


def _assemble(author: str, year: str, source: str, volume: str,
              page: str, doi: str) -> str:
    """Join the extracted fields using WoS field order and punctuation."""
    parts = [author, year]
    if source:
        parts.append(source)
    if volume:
        parts.append("V" + volume)
    if page:
        parts.append("P" + page)
    if doi:
        parts.append("DOI " + doi)
    return ", ".join(parts)


# ── Per-format converters ────────────────────────────────────────────────

def _from_new_format(ref: str) -> str | None:
    """Convert ``Authors, Title, Source, Vol, Issue, pp. X-Y, (YYYY)``."""
    m = _TRAILING_YEAR.search(ref)
    if m is None:
        return None
    year = m.group(1)
    body = ref[:m.start()].strip().strip(",").strip()
    if not body:
        return None

    # Leading empty tokens come from malformed Scopus output such as
    # "...Handbook of Robotics;, pp. 1127-1150, (2014)"; dropping them keeps the
    # author in slot 0 and the result stable under re-normalization.
    tokens = [t.strip() for t in body.split(",")]
    while tokens and not tokens[0]:
        tokens.pop(0)
    if not tokens:
        return None
    author = _first_author(tokens[0])
    if not author:
        return None

    vol_idx = _pick_volume(tokens, year)
    volume = tokens[vol_idx] if vol_idx is not None else ""

    # Source: the token right before the volume.  Index 0 is the author block
    # and index 1 is the title, so a source is only credible from index 2 on —
    # "Authors, Title, 33, 5, (2000)" (Scopus dropped the journal) must not turn
    # its title into a source.
    source = ""
    if vol_idx is not None:
        if vol_idx - 1 >= 2:
            source = _clean_source(tokens[vol_idx - 1])
    else:
        # No volume: fall back to the last non-page, non-number token that is
        # not the title, which is where books and proceedings carry the source.
        for j in range(len(tokens) - 1, 1, -1):
            source = _clean_source(tokens[j])
            if source:
                break

    page_m = _PAGES.search(body)
    page = page_m.group(1) if page_m else ""
    doi_m = _DOI.search(ref)
    doi = doi_m.group(1).rstrip(".,;") if doi_m else ""

    return _assemble(author, year, source, volume, page, doi)


def _from_classic_format(ref: str) -> str | None:
    """Convert ``Author1, Author2, Title (YYYY) Journal, Vol, pp. X-Y``."""
    m = _CLASSIC_YEAR.search(ref)
    if m is None:
        return None
    year = m.group(1)
    author = _first_author(ref[:m.start()])
    if not author:
        return None

    tail = ref[m.end():]
    tokens = [t.strip() for t in tail.split(",")]
    while tokens and not tokens[0]:
        tokens.pop(0)
    source = _clean_source(tokens[0]) if tokens else ""

    vol_idx = _pick_volume(tokens, year, start=1)
    volume = tokens[vol_idx] if vol_idx is not None else ""

    page_m = _PAGES.search(tail)
    page = page_m.group(1) if page_m else ""
    doi_m = _DOI.search(ref)
    doi = doi_m.group(1).rstrip(".,;") if doi_m else ""

    return _assemble(author, year, source, volume, page, doi)


def _neutralize(ref: str) -> str:
    """Keep an unconvertible reference, but make it safe for a ``;`` split.

    Internal semicolons become commas so the WoS writer cannot shred the
    reference into fragments; the text is otherwise preserved so VOSviewer can
    still match it verbatim (its rule-4 fallback).
    """
    out = _squeeze(ref.replace(";", ","))
    out = _MULTI_COMMA.sub(", ", out)
    return out.strip(" ,")


def _convert_ref(ref: str) -> str:
    """Normalize one reference; never returns an empty string for real input.

    WoS grammar is tested **first**: a real WoS reference can end in a
    parenthesised number that looks like a year
    (``DOI 10.1061/(ASCE)0733-9445(2005)131:11(1656)``), and treating it as
    Scopus input would move the DOI's page number into the year slot.
    """
    if _WOS_REF.match(ref):
        # Already WoS grammar — only unify the author spelling (comma variant).
        return _strip_wos_author_comma(_squeeze(ref))
    if _TRAILING_YEAR.search(ref):
        converted = _from_new_format(ref)
        if converted:
            return converted
    elif _CLASSIC_YEAR.search(ref):
        converted = _from_classic_format(ref)
        if converted:
            return converted
    return _neutralize(ref)


# ── Cell / chunk splitting ───────────────────────────────────────────────

# A bare author name: "Fahle L.", "Di Maggio R. M." or the comma variant
# "Mccallum, A." whose tail is nothing but single-letter initials.
_AUTHOR_FRAGMENT = re.compile(
    r"^[^,;\d]{1,40}(?:,\s*[A-Za-z]\b\.?(?:[\s.-]+[A-Za-z]\b\.?)*)?\s*$"
)


def _is_author_fragment(part: str) -> bool:
    """True when a ``;``-part is a Scopus author name, not a whole reference.

    Scopus writes ``"Fahle L.; Holley E.A.; Walton G., Title, ..."`` — every
    part but the last is a short, year-free name, sometimes in the
    ``"Mccallum, A."`` surname-comma-initials shape.  Recognising them is what
    stops a ``;`` split from shredding one reference into five.
    """
    return len(part) <= 60 and bool(_AUTHOR_FRAGMENT.match(part))


def _split_chunk(chunk: str) -> list[str]:
    """Split a chunk into references, keeping author blocks glued together.

    Semicolons play both roles in Scopus data, so parts are re-glued left to
    right: an author fragment always belongs to the reference that follows it,
    and the first part that is not a fragment closes the current reference.
    """
    if ";" not in chunk:
        return [chunk]
    refs: list[str] = []
    buf: list[str] = []
    for part in chunk.split(";"):
        if not part.strip():
            continue
        buf.append(part)
        if not _is_author_fragment(part.strip()):
            refs.append(";".join(buf).strip())
            buf = []
    if buf:
        refs.append(";".join(buf).strip())
    return [r for r in refs if r]


def _is_wos_cell(cell: str) -> bool:
    """True when the whole cell is already WoS grammar (fast, lossless path).

    A plain ``;`` split is safe for detection: WoS references never contain a
    semicolon, while any Scopus reference in the cell shows up as a part with a
    ``(yyyy)`` marker and vetoes the fast path.
    """
    refs = [r.strip() for r in cell.split(";") if r.strip()]
    if not refs:
        return False
    wos_n = 0
    has_fragment = False
    for ref in refs:
        if _WOS_REF.match(ref):
            wos_n += 1
        elif _TRAILING_YEAR.search(ref) or _CLASSIC_YEAR.search(ref):
            return False              # at least one Scopus reference present
        elif _is_author_fragment(ref):
            has_fragment = True
    if wos_n:
        return True                   # WoS-dominated cell — hand it back as is
    # No WoS reference and no Scopus marker: the cell only carries year-less
    # entries ("Agisoft, Agisoft Metashape User Manual").  Nothing to convert,
    # so it can stay untouched as long as no ";" hides an author block.
    return not has_fragment


# ── Public API ───────────────────────────────────────────────────────────

def normalize_cr(cr: str) -> str:
    """Rewrite a CR cell into Web of Science reference grammar.

    Scopus new-format and classic-format references are converted to
    ``AUTHOR, YYYY[, SOURCE][, Vvol][, Ppage][, DOI doi]``; references already
    in WoS grammar pass through untouched; anything unconvertible is preserved
    with its internal ``;`` neutralized to ``,``.  References are joined with
    ``"; "``.

    The function is idempotent and tolerates ``None``, ``NaN`` and non-string
    input (returns ``""`` for empty/missing values).
    """
    if cr is None:
        return ""
    if not isinstance(cr, str):
        if cr != cr:                  # NaN (float/np.nan) — NaN != NaN
            return ""
        cr = str(cr)

    stripped = cr.strip()
    if not stripped or stripped.lower() == "nan":
        return ""

    # Already-WoS cells skip the reference-level pipeline; only the author
    # spelling is unified (comma-less input stays byte-identical — the comma
    # pattern simply never matches it).
    if _is_wos_cell(stripped):
        return _strip_wos_author_comma(cr)

    out: list[str] = []
    # Split at ";" directly after a trailing "(yyyy)" first — the only boundary
    # that is unambiguous in a Scopus new-format cell.  Whatever the resulting
    # chunk holds (a Scopus reference whose ";" are author separators, or a run
    # of classic/WoS references) is decided by _split_chunk.
    for chunk in _NEW_BOUNDARY.split(stripped):
        chunk = chunk.strip()
        if not chunk:
            continue
        for sub in _split_chunk(chunk):
            sub = sub.strip()
            if sub:
                out.append(_convert_ref(sub))

    return "; ".join(r for r in out if r)


def count_refs(cr: str) -> int:
    """Count the references in a (normalized) CR cell — used to fill ``NR``."""
    if cr is None or not isinstance(cr, str):
        return 0
    return sum(1 for part in cr.split(";") if part.strip())
