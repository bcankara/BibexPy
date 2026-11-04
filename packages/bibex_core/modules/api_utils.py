from __future__ import annotations

import requests
import pandas as pd
import os
import re
import json
import time
import difflib
import html
import unicodedata
from dataclasses import dataclass, field as dc_field
import functools
import threading
from collections import OrderedDict
from functools import lru_cache
from urllib.parse import quote, urlparse
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# (connect, read) timeout for all external API calls — if a single API hangs,
# this prevents the entire enrichment job from locking up indefinitely.
REQUEST_TIMEOUT = (5, 20)

# Longest Retry-After (seconds) the HTTP helpers are willing to sleep for. A longer wait is
# reported to the caller immediately instead of being slept off: the old helper slept
# 3 x 15 s and then returned nothing, so an exhausted API quota cost ~48 s per record and
# looked exactly like "no match".
MAX_RETRY_AFTER = 5.0

_OPENALEX_HOST = "api.openalex.org"
_openalex_default_key = ""            # set via set_openalex_api_key(); applied to every OpenAlex call
_host_min_interval: dict = {}         # host -> minimum seconds between calls (learned from rate-limit headers)
_host_last_call: dict = {}            # host -> time.monotonic() of the previous call


def set_openalex_api_key(key) -> None:
    """Use `key` for every OpenAlex request made through `_http_get` (empty/None clears it).

    The key is free (https://openalex.org/settings/api). Without one, OpenAlex search calls
    draw on a small daily budget shared by everyone behind the same IP, while singleton
    `/works/doi:X` lookups are free. The key is never logged.
    """
    global _openalex_default_key
    _openalex_default_key = str(key or "").strip()


def _hdr(r, name: str):
    """Case-insensitive response-header read that also works for plain-dict test stubs."""
    headers = getattr(r, "headers", None) or {}
    try:
        val = headers.get(name)
    except Exception:
        val = None
    if val is None:
        low = name.lower()
        try:
            for k, v in dict(headers).items():
                if str(k).lower() == low:
                    return v
        except Exception:
            pass
    return val


def _retry_after(r):
    """Retry-After header in seconds, or None when absent / not numeric."""
    val = _hdr(r, "Retry-After")
    try:
        return max(0.0, float(val))
    except (TypeError, ValueError):
        return None


def _pace(host: str) -> None:
    """Sleep just long enough to respect the per-host pace learned from rate-limit headers."""
    gap = _host_min_interval.get(host)
    if gap:
        wait = gap - (time.monotonic() - _host_last_call.get(host, 0.0))
        if 0 < wait <= 2.0:
            time.sleep(wait)
    _host_last_call[host] = time.monotonic()


def _note_rate_headers(host: str, r) -> None:
    """Learn the host's pace from Crossref-style `x-rate-limit-limit` / `x-rate-limit-interval`."""
    try:
        limit = float(_hdr(r, "x-rate-limit-limit"))
        interval = float(str(_hdr(r, "x-rate-limit-interval")).strip().rstrip("s"))
    except (TypeError, ValueError):
        return
    if limit > 0 and interval > 0:
        _host_min_interval[host] = min(interval / limit, 2.0)


def _http_get(url: str, params: dict | None = None, headers: dict | None = None):
    """ONE HTTP GET attempt (timeout, host pacing, OpenAlex key injection; no retries).

    Every request of this module goes through here, so tests and the benchmark harness can
    monkeypatch this single function to record/replay API traffic without any network.
    """
    host = urlparse(url).netloc.lower()
    if host == _OPENALEX_HOST and _openalex_default_key:
        has_auth = any(str(k).lower() == "authorization" for k in (headers or {}))
        if not has_auth and "api_key" not in (params or {}) and "api_key=" not in url:
            params = dict(params or {})
            params["api_key"] = _openalex_default_key
    _pace(host)
    r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    _note_rate_headers(host, r)
    return r


def _get_with_retry(url: str, *, headers: dict | None = None, attempts: int = 3, params: dict | None = None):
    """`_http_get` + short backoff on network errors and 429/503.

    Retry-After is honoured only up to MAX_RETRY_AFTER seconds; a longer wait means a hard
    quota, so the response is returned at once and the caller sees a non-OK status.
    """
    last = None
    for i in range(attempts):
        try:
            r = _http_get(url, params, headers)
        except requests.RequestException as e:
            last = e
            if i < attempts - 1:
                time.sleep(min(2 ** i, 4))
            continue
        if r.status_code in (429, 503):
            delay = _retry_after(r)
            if delay is None:
                delay = min(2 ** i, 4)
            last = r
            if delay > MAX_RETRY_AFTER or i == attempts - 1:
                return r
            time.sleep(delay)
            continue
        return r
    if isinstance(last, requests.Response) or hasattr(last, "status_code"):
        return last
    raise last if last else requests.RequestException(f"request failed: {url}")


# ── Reverse DOI lookup (find a missing DI from title + year + author) ───────────
#
# PRECISION FIRST. A wrong DOI is far worse than a missing one: Smart Merge treats a DOI as
# determinative, and the enrichment passes that follow import the abstract / keywords /
# affiliations of whatever work the DOI points to. The pipeline is therefore:
#
#   structured candidates (Crossref first, OpenAlex only as a fallback)
#     -> type policy -> year -> ISSN -> authors -> token-aligned title comparison
#     -> preprint handling -> score ranking (class only breaks exact ties) -> abstain on ties and on DOIs
#        that already sit on another row.
#
# Everything that can say "no" does so; the function never fabricates a value.

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"

CROSSREF_SELECT = (
    "DOI,title,subtitle,type,issued,published,published-online,published-print,"
    "container-title,short-container-title,ISSN,author,volume,page,relation"
)
OPENALEX_SELECT = "id,doi,title,display_name,publication_year,type,primary_location,authorships,ids"
CROSSREF_ROWS = 10
OPENALEX_ROWS = 5
# Per-run cache bound (a long run would otherwise keep every Crossref answer in memory).
CACHE_MAX_ENTRIES = 512
# Consecutive failed requests (network error, 5xx, short 429) after which a source is given up for the run.
MAX_FAIL_STREAK = 8

# Two DIFFERENT DOIs at the top whose ranking scores differ by less than this -> abstain (any class).
SCORE_MARGIN = 0.02
# Remaining OpenAlex credits below which the source is treated as exhausted (a search costs 10).
OPENALEX_MIN_CREDITS = 10
# Retry-After above this many seconds is treated as a hard quota (circuit breaker).
HARD_QUOTA_SECONDS = 60.0

# Type policy. Anything outside the allow-list (peer-review, component, dataset,
# report-component, grant, standard, ...) is rejected outright.
_ALLOWED_TYPES = frozenset({
    "journal-article", "proceedings-article", "book-chapter", "book", "monograph",
    "edited-book", "reference-entry", "report", "dissertation", "posted-content", "other",
})
# Class: lower is better. Used only as a sort tie-break (and to tell preprints apart); the
# ranking itself is by evidence score.
_TYPE_CLASS = {
    "journal-article": 0, "proceedings-article": 0,
    "book-chapter": 1, "reference-entry": 1,
    "book": 2, "monograph": 2, "edited-book": 2, "report": 2, "dissertation": 2,
    "other": 3,
    "posted-content": 4,
}
_PREPRINT_CLASS = 4

# DOI prefixes of preprint servers (SSRN, OSF family, Preprints.org, Research Square, arXiv,
# Authorea, ChemRxiv, TechRxiv, engrXiv). 10.1101 is shared with Cold Spring Harbor journals,
# so only the bioRxiv/medRxiv DOI shapes count there.
_PREPRINT_PREFIXES = (
    "10.2139/", "10.31234/", "10.31219/", "10.31222/", "10.31223/", "10.31235/",
    "10.20944/", "10.21203/", "10.48550/", "10.22541/", "10.26434/", "10.36227/", "10.31224/",
)
# Data / software repository DOIs (Zenodo, figshare, OSF storage, Harvard Dataverse, Dryad,
# Mendeley Data): self-archived copies, never the publisher's DOI of a paper. They are
# handled exactly like preprints (preprint_only, with a repository warning).
_REPOSITORY_PREFIXES = (
    "10.5281/", "10.6084/", "10.17605/", "10.7910/", "10.5061/", "10.17632/",
)
_BIORXIV_DOI_RE = re.compile(r"^10\.1101/(?:\d{4}\.\d{2}\.\d{2}\.\d+|\d{5,7})(?:v\d+)?$")
_PREPRINT_CONTAINER_RE = re.compile(
    r"\b(arxiv|ssrn|biorxiv|medrxiv|chemrxiv|techrxiv|engrxiv|psyarxiv|socarxiv|preprints|"
    r"research square|authorea|osf preprints)\b"
)
_REPOSITORY_CONTAINER_RE = re.compile(r"\b(zenodo|figshare|dryad|dataverse|mendeley data|osf)\b")
# A record whose own source is a preprint server may legitimately get a preprint DOI.
_PREPRINT_SOURCE_RE = re.compile(
    r"arxiv|ssrn|research square|medrxiv|biorxiv|preprints|chemrxiv|authorea|osf preprints|"
    r"psyarxiv|socarxiv|techrxiv"
)
# Logged as a signal only (never a rejection rule): DOI shapes of peer-review reports and
# supporting information.
_SUSPICIOUS_DOI_RE = re.compile(r"/review\d|\.s\d{3}$|/v\d+/review", re.I)

_DOI_PREFIX_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.I)
_DOI_SEP_FOLD_RE = re.compile(r"[_\u2010-\u2015]")


def clean_doi(raw) -> str:
    """Lowercase DOI without resolver prefix / 'doi:' / trailing punctuation ('' when not a DOI).

    This is the form WRITTEN into the dataset (separators are NOT altered: '_' and '-' can
    be different characters of a real DOI).
    """
    s = str(raw or "").strip().lower()
    for _ in range(4):
        prev = s
        s = _DOI_PREFIX_RE.sub("", s)
        s = re.sub(r"^doi\s*:\s*", "", s)
        s = re.sub(r"^doi\.org/", "", s)
        if s == prev:
            break
    s = s.rstrip("/. \t")
    return s if s.startswith("10.") else ""


def doi_key(raw) -> str:
    """Comparison key of a DOI — identical to smart_merger.normalize_doi (separators folded).

    Use this for the `existing` set passed to lookup_doi: the indexers write the same DOI
    with '_' (WoS) or '-' (Scopus), and Smart Merge compares DOIs in this folded form.
    """
    return _DOI_SEP_FOLD_RE.sub("-", clean_doi(raw))


# ── Text normalization ──────────────────────────────────────────────────────────
_INLINE_TAG_RE = re.compile(
    r"</?(?:i|b|u|em|strong|sub|sup|sc|tt|span|italic|bold|underline|small|big|math|inline-formula|"
    r"mml:[\w-]+|jats:(?:italic|bold|sub|sup|underline|sc|monospace))(?:\s[^<>]*)?/?>", re.I)
_ANY_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,200}>")
_FOLD_TABLE = str.maketrans({
    "ß": "ss", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "ı": "i", "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D",
})
# Apostrophes are dropped (not turned into spaces); U+FFFD is what a broken apostrophe
# looks like in mis-decoded WoS/Scopus exports.
_APOSTROPHES = dict.fromkeys(map(ord, "'\u2019\u2018\u02bc`\u00b4\ufffd"), None)
_ROMAN = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8",
    "ix": "9", "x": "10", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15",
    "xvi": "16", "xvii": "17", "xviii": "18", "xix": "19", "xx": "20",
}
_STOP = frozenset({
    "the", "a", "an", "of", "in", "on", "and", "or", "for", "to", "with", "by", "from", "as",
    "at", "is", "are", "was", "were", "be", "been",
})
# "Part A", "Study 2", "Phase II": the number/letter after these words is meaningful even
# when it is a stop word ('a').
_SERIES_WORDS = frozenset({
    "part", "phase", "stage", "study", "experiment", "trial", "volume", "vol", "chapter",
    "section", "wave", "book", "paper", "session", "series", "edition", "version", "number",
    "no", "season", "episode", "module", "lecture", "report", "case", "step", "round", "cohort",
})
_MARKER_WORDS = {
    "erratum": "erratum", "errata": "erratum", "corrigendum": "corrigendum",
    "corrigenda": "corrigendum", "correction": "correction", "corrections": "correction",
    "comment": "comment", "comments": "comment", "commentary": "commentary",
    "reply": "reply", "replies": "reply", "response": "response", "responses": "response",
    "retraction": "retraction", "retracted": "retracted", "withdrawn": "withdrawn",
    "editorial": "editorial", "addendum": "addendum", "letter": "letter", "letters": "letter",
    "rebuttal": "rebuttal", "rejoinder": "rejoinder", "erratas": "erratum",
    "berichtigung": "erratum", "rectificatif": "erratum", "rectificativo": "erratum",
    "duzeltme": "erratum", "duzeltmesi": "erratum",
}
_MARKER_PHRASES = (
    ("book review", "book review"), ("review of", "review of"),
    ("expression of concern", "expression of concern"),
    ("author correction", "author correction"), ("publisher correction", "publisher correction"),
    ("duplicate publication", "duplicate publication"), ("fe de erratas", "erratum"),
    ("supporting information", "supporting information"),
)
# Words that may never sit in a segment omitted from one side of a title ("T: Update",
# "T: Slides", "T: Supplementary Information"). Kept apart from the markers above because a
# generic word like 'update' or 'closure' is a legitimate part of many ordinary titles, where
# it must not create a marker mismatch between two otherwise equal titles.
_OMISSION_BLOCK_WORDS = frozenset({
    "rebuttal", "rejoinder", "closure", "discussion", "discussions", "update", "updates",
    "notice", "supplement", "supplements", "supplementary", "supplemental", "reprint", "reprinted",
    "slides", "slide", "poster", "posters", "presentation", "presentations", "erratas",
    "berichtigung", "rectificatif", "rectificativo", "duzeltme", "duzeltmesi",
    "video", "videos", "appendix", "appendices",
})
_OMISSION_BLOCK_PHRASES = (
    "duplicate publication", "fe de erratas", "supporting information", "supplementary information",
    "supplementary material", "supplementary materials", "expression of concern",
    "graphical abstract",
)
# Blocked only at the start of the omitted segment ('Re: T', 'Data from: T', 'Abstract of T').
_OMISSION_BLOCK_LEAD = ("re", "data from", "abstract of", "abstracts of", "notice of", "comment on")
_GENERIC_TITLES = frozenset({
    "editorial", "introduction", "preface", "foreword", "conclusion", "conclusions",
    "letter to the editor", "letter to editor", "book review", "book reviews", "index",
    "contents", "table of contents", "abstract", "abstracts", "erratum", "errata", "correction",
    "corrigendum", "comment", "reply", "response", "announcement", "announcements", "news",
    "obituary", "acknowledgments", "acknowledgements", "reviewers", "front matter",
    "back matter", "editor s note", "editors note", "guest editorial", "welcome", "title page",
    "cover", "masthead", "editorial board", "author index", "subject index",
})


def _s(v) -> str:
    """Cell value -> stripped string; None / NaN / 'nan' / 'None' become ''."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def _strip_markup(raw) -> str:
    """HTML entities, HTML/MathML tags and LaTeX commands out of a title."""
    s = html.unescape(str(raw or ""))
    s = _INLINE_TAG_RE.sub("", s)
    s = _ANY_TAG_RE.sub(" ", s)
    if "\\" in s:
        s = re.sub(r"\\[A-Za-z]+\*?", " ", s)
        s = re.sub(r"[{}$^]", "", s)
    return s


def _fold(s: str) -> str:
    """Accent/ligature fold to ASCII where one exists; letters of other scripts are KEPT."""
    s = s.translate(_FOLD_TABLE)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _norm_text(raw) -> str:
    """Markup-free, accent-folded, lowercase text where every non-word run is one space."""
    s = _fold(_strip_markup(raw)).casefold().translate(_APOSTROPHES)
    s = s.replace("&", " and ")
    return re.sub(r"[\W_]+", " ", s).strip()


def _tokens(raw) -> list:
    """Normalized tokens with standalone roman numerals I-XX turned into arabic."""
    return [_ROMAN.get(t, t) for t in _norm_text(raw).split()]


def _content(tokens) -> list:
    """Tokens without stop words (a single letter after 'part'/'study'/... is kept)."""
    out, prev = [], ""
    for t in tokens:
        if t not in _STOP or (len(t) == 1 and prev in _SERIES_WORDS):
            out.append(t)
        prev = t
    return out


def _markers(tokens) -> frozenset:
    found = {_MARKER_WORDS[t] for t in tokens if t in _MARKER_WORDS}
    text = " " + " ".join(tokens) + " "
    for phrase, canon in _MARKER_PHRASES:
        if f" {phrase} " in text:
            found.add(canon)
    return frozenset(found)


_TRAILING_BRACKET_RE = re.compile(r"\s*\[[^\[\]]{3,}\]\s*\.?\s*$")


def _title_forms(raw) -> list:
    """Token lists of a title: as given, plus without a trailing bracketed translation."""
    s = str(raw or "")
    forms = [_tokens(s)]
    m = _TRAILING_BRACKET_RE.search(s)
    # (not when the bracket carries a marker: 'T [Retracted]', 'T [Video]' are other items)
    if m and m.start() > 0 and not _is_blocked_text(_tokens(m.group(0))):
        stripped = _tokens(s[:m.start()])
        if stripped and stripped != forms[0]:
            forms.append(stripped)
        # The bracketed translation as a whole title of its own ('Title; [Título]' vs a
        # candidate that only carries the Spanish title). A full-title comparison, not an
        # omission, so no extra tolerance is involved.
        translated = _tokens(m.group(0).strip().strip(".").strip().strip("[]"))
        # (not when the title carries a marker word: 'Erratum to: T; [translation]' is no match)
        if len(translated) >= 4 and translated not in forms and not _markers(forms[0]):
            forms.append(translated)
    return [f for f in forms if f]


def _within_one_edit(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _aligned(a: list, b: list):
    """Token-aligned equality -> (ok, n_variants).

    Same token sequence. A spelling variant (edit distance 1) is tolerated only inside an
    alphabetic ASCII token of >= 5 characters, so a changed number or a short word
    (with/without, 2/3, before/after) never passes. Differences of spacing/hyphenation
    ('e-learning' vs 'elearning') pass when the joined strings are identical.
    """
    if a == b:
        return True, 0
    if len(a) == len(b):
        variants = 0
        for x, y in zip(a, b):
            if x == y:
                continue
            if (len(x) >= 5 and len(y) >= 5 and x.isascii() and y.isascii()
                    and x.isalpha() and y.isalpha() and _within_one_edit(x, y)):
                variants += 1
                continue
            return False, 0
        return True, variants
    if a and b and "".join(a) == "".join(b):
        return True, 1
    return False, 0


_SEGMENT_SPLIT_RE = re.compile(r"\s*[:?!;]\s+|(?<=\w)\.\s+|\s+[-\u2013\u2014\u2015]\s+")


def _omission_ok(omitted_tokens) -> bool:
    """May this leading/trailing segment be missing on the other side?

    Never when it looks like a series designator ('Part II', 'Study 2', '2nd edition'),
    is a short numeric tail, or carries a marker word (erratum, reply, ...).
    """
    if not omitted_tokens:
        return False
    if omitted_tokens[0] in _SERIES_WORDS:
        return False
    for a, b in zip(omitted_tokens, omitted_tokens[1:]):
        if a in _SERIES_WORDS and (b.isdigit() or len(b) == 1):
            return False             # '... . Part II', 'Study 2' anywhere in the omitted part
    if len(omitted_tokens) <= 3 and any(t.isdigit() for t in omitted_tokens):
        return False
    return not _is_blocked_text(omitted_tokens)


def _is_blocked_text(tokens) -> bool:
    """Marker words or omission-blocked words/phrases (reply, update, slides, ...) in `tokens`."""
    if _markers(tokens):
        return True
    if any(t in _OMISSION_BLOCK_WORDS for t in tokens):
        return True
    text = " " + " ".join(tokens) + " "
    if any(f" {p} " in text for p in _OMISSION_BLOCK_PHRASES):
        return True
    lead = " ".join(tokens) + " "
    return any(lead.startswith(p + " ") for p in _OMISSION_BLOCK_LEAD)


def _subtitle_relation(short_raw: str, long_raw: str) -> bool:
    """True when `short_raw` equals `long_raw` minus a TRAILING subtitle segment.

    Only a suffix may be missing ('Main title' vs 'Main title: subtitle'). A candidate with
    extra text IN FRONT of the shared title ('Re: T', 'Rebuttal: T', 'Notice ...: T') is a
    different item (reply, notice, slides, ...) and never matches.
    """
    st = _content(_tokens(short_raw))
    if len(st) < 4:
        return False
    segs = [p for p in _SEGMENT_SPLIT_RE.split(_strip_markup(long_raw)) if p and p.strip()]
    if len(segs) < 2:
        return False
    short_markers = _markers(_tokens(short_raw))
    for k in range(1, len(segs)):
        head, tail = " ".join(segs[:k]), " ".join(segs[k:])
        kt = _content(_tokens(head))
        if len(kt) < 4:
            continue
        ok, _ = _aligned(st, kt)
        if ok and _markers(_tokens(head)) == short_markers and _omission_ok(_tokens(tail)):
            return True
    return False


# ── Candidates ──────────────────────────────────────────────────────────────────
@dataclass
class Candidate:
    """One work returned by Crossref or OpenAlex, reduced to what the checks need."""
    doi: str                                   # canonical lowercase
    title: str = ""
    subtitle: str = ""
    type: str = ""                             # Crossref vocabulary (OpenAlex types are mapped)
    years: set = dc_field(default_factory=set)    # every year from issued/published/published-*
    container: list = dc_field(default_factory=list)   # container names incl. short names
    issn: set = dc_field(default_factory=set)     # digits + X, uppercase, no hyphen
    authors: list = dc_field(default_factory=list)     # first 5 as (family, given)
    volume: str = ""
    page: str = ""
    relation: dict = dc_field(default_factory=dict)
    source: str = "crossref"                   # 'crossref' | 'openalex'
    rank: int = 0                              # position in the API response
    loose_names: bool = False                  # True: authors are (""), "Given Family" display names

    @property
    def is_preprint(self) -> bool:
        d = self.doi.lower()
        if self.type == "posted-content":
            return True
        if d.startswith(_PREPRINT_PREFIXES) or _BIORXIV_DOI_RE.match(d) or self.is_repository:
            return True
        return any(_PREPRINT_CONTAINER_RE.search(_norm_text(c)) for c in self.container)

    @property
    def is_repository(self) -> bool:
        """Data / software repository copy (Zenodo, figshare, Dryad, Dataverse, OSF, Mendeley Data)."""
        if self.doi.lower().startswith(_REPOSITORY_PREFIXES):
            return True
        return any(_REPOSITORY_CONTAINER_RE.search(_norm_text(c)) for c in self.container)


_ISSN_FIND_RE = re.compile(r"(?<![\d-])\d{4}-?\d{3}[\dXx](?!\d)")


def _issn_set(*values) -> set:
    out = set()
    for v in values:
        items = v if isinstance(v, (list, tuple, set)) else [v]
        for it in items:
            for m in _ISSN_FIND_RE.findall(str(it or "")):
                out.add(m.replace("-", "").upper())
    return out


def _crossref_years(item: dict) -> set:
    years = set()
    for k in ("issued", "published", "published-online", "published-print"):
        parts = (item.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            try:
                years.add(int(parts[0][0]))
            except (TypeError, ValueError):
                pass
    return years


def _first(v) -> str:
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else ""
    return str(v or "")


def _candidate_from_crossref(item: dict, rank: int):
    doi = clean_doi(item.get("DOI"))
    if not doi:
        return None
    containers = [str(c) for c in (item.get("container-title") or []) if c]
    containers += [str(c) for c in (item.get("short-container-title") or []) if c]
    authors = []
    for a in (item.get("author") or [])[:5]:
        if isinstance(a, dict):
            authors.append((str(a.get("family") or a.get("name") or ""), str(a.get("given") or "")))
    return Candidate(
        doi=doi,
        title=_first(item.get("title")),
        subtitle=" ".join(str(x) for x in (item.get("subtitle") or [])),
        type=str(item.get("type") or ""),
        years=_crossref_years(item),
        container=containers,
        issn=_issn_set(item.get("ISSN") or []),
        authors=authors,
        volume=str(item.get("volume") or ""),
        page=str(item.get("page") or ""),
        relation=item.get("relation") if isinstance(item.get("relation"), dict) else {},
        source="crossref",
        rank=rank,
    )


_OPENALEX_TYPE_MAP = {
    "article": "journal-article", "review": "journal-article", "editorial": "journal-article",
    "letter": "journal-article", "preprint": "posted-content",
}


def _candidate_from_openalex(work: dict, rank: int):
    doi = clean_doi(work.get("doi"))
    if not doi:
        return None
    ploc = work.get("primary_location") if isinstance(work.get("primary_location"), dict) else {}
    source = ploc.get("source") if isinstance(ploc.get("source"), dict) else {}
    containers = [source["display_name"]] if source.get("display_name") else []
    authors = []
    for a in (work.get("authorships") or [])[:5]:
        name = ((a or {}).get("author") or {}).get("display_name")
        if name:
            authors.append(("", str(name)))
    year = work.get("publication_year")
    otype = str(work.get("type") or "")
    return Candidate(
        doi=doi,
        title=str(work.get("title") or work.get("display_name") or ""),
        type=_OPENALEX_TYPE_MAP.get(otype, otype),
        years={int(year)} if isinstance(year, int) or str(year or "").isdigit() else set(),
        container=containers,
        issn=_issn_set(source.get("issn") or [], source.get("issn_l")),
        authors=authors,
        source="openalex",
        rank=rank,
        loose_names=True,
    )


# ── Authors ─────────────────────────────────────────────────────────────────────
_PARTICLES = frozenset({
    "van", "von", "der", "de", "la", "del", "da", "di", "du", "le", "al", "el", "bin",
    "den", "ter", "ten", "dos", "das", "do", "y", "ibn",
})
_PAREN_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")


@dataclass(frozen=True)
class _RAuthor:
    fam: frozenset      # family-name tokens (accent-folded, particles dropped)
    initial: str        # first initial ('' when the source gave none)
    family: str         # printable family name


def _family_tokens(tokens) -> frozenset:
    toks = [t for t in tokens if len(t) >= 2]
    kept = [t for t in toks if t not in _PARTICLES]
    return frozenset(kept or toks)


def parse_record_authors(raw, limit: int = 3) -> list:
    """AU/AF string -> first `limit` authors as _RAuthor.

    Accepts WoS 'SURNAME, I', Scopus 'SURNAME I', AF 'SURNAME, GIVEN (id)' and
    'zhao (<native name>)'. Parenthesised parts and name particles are dropped.
    """
    out = []
    for part in str(raw or "").split(";"):
        part = _PAREN_RE.sub(" ", part).strip()
        if not part:
            continue
        if "," in part:
            fam_s, given_s = part.split(",", 1)
            fam_tokens = _norm_text(fam_s).split()
            given_tokens = _norm_text(given_s).split()
            initial = given_tokens[0][:1] if given_tokens else ""
        else:
            raw_tokens = part.split()
            norm_tokens = _norm_text(part).split()
            last = raw_tokens[-1].replace(".", "") if raw_tokens else ""
            if len(raw_tokens) >= 2 and last and len(last) <= 3 and (last.isupper() or len(last) == 1):
                # 'SALGADO GRANDA BS' -> family 'salgado granda', initials 'BS'
                fam_tokens = _norm_text(" ".join(raw_tokens[:-1])).split()
                initial = _norm_text(last)[:1]
            else:
                fam_tokens, initial = norm_tokens, ""
        fam = _family_tokens(fam_tokens)
        if fam:
            kept = [t for t in fam_tokens if t in fam]
            out.append(_RAuthor(fam=fam, initial=initial, family=" ".join(kept)))
        if len(out) >= limit:
            break
    return out


def _authors_agree(rec_authors: list, cand: Candidate) -> bool:
    """Any of the first 3 record authors matches any of the first 3 candidate authors.

    A match needs a shared family-name token (>= 2 characters) and, when both sides give an
    initial, the same first initial.
    """
    for ra in rec_authors[:3]:
        for family, given in cand.authors[:3]:
            if cand.loose_names:
                # OpenAlex only gives a display name: any token may be the family name.
                toks = _norm_text(given).split()
                shared = ra.fam & set(toks)
                if not shared:
                    continue
                initials = {t[0] for t in toks if t not in ra.fam}
                if ra.initial and initials and ra.initial not in initials:
                    continue
                return True
            fam = _family_tokens(_norm_text(_PAREN_RE.sub(" ", family)).split())
            if not (ra.fam & fam):
                continue
            gtoks = _norm_text(given).split()
            ci = gtoks[0][:1] if gtoks else ""
            if ra.initial and ci and ra.initial != ci:
                continue
            return True
    return False


# ── Container / ISSN ────────────────────────────────────────────────────────────
def _norm_container(s) -> str:
    return re.sub(r"^the\s+", "", _norm_text(s))


def _container_sim(rec_names: list, cand_names: list) -> float:
    """Best similarity between any record journal name and any candidate container name."""
    best = 0.0
    for a in rec_names:
        na = _norm_container(a)
        if not na:
            continue
        ta = set(na.split())
        for b in cand_names:
            nb = _norm_container(b)
            if not nb:
                continue
            tb = set(nb.split())
            jacc = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
            best = max(best, difflib.SequenceMatcher(None, na, nb).ratio(), jacc)
    return best


# ── Title comparison ────────────────────────────────────────────────────────────
def title_match(record_title, cand: Candidate, allow_subtitle: bool = True):
    """Compare a record title with a candidate -> (ok, score, why).

    The record title is compared with `cand.title` and `cand.title + subtitle`. `why` is
    'exact', 'variant' (spelling variants) or 'subtitle' (one side lacks a subtitle) on
    success, otherwise the reason for the rejection. Subtitle omission needs a shared main
    part of >= 4 content tokens; the CALLER must additionally require author and year
    agreement for it (lookup_doi does, via allow_subtitle).
    """
    rec_forms = _title_forms(record_title)
    cand_raws = [cand.title] + ([f"{cand.title}: {cand.subtitle}"] if cand.subtitle else [])
    cand_forms = [f for raw in cand_raws for f in _title_forms(raw)]
    if not rec_forms or not cand_forms:
        return False, 0.0, "empty title"
    best, marker_clash = None, ""
    for rf in rec_forms:
        rc = _content(rf)
        if not rc:
            continue
        for cf in cand_forms:
            cc = _content(cf)
            if not cc:
                continue
            ok, variants = _aligned(rc, cc)
            if not ok:
                continue
            mr, mc = _markers(rf), _markers(cf)
            if mr != mc:
                marker_clash = ",".join(sorted(mr ^ mc))
                continue
            score = 1.0 - 0.01 * min(variants, 5)
            if best is None or score > best[0]:
                best = (score, "exact" if variants == 0 else "variant")
    if best:
        return True, round(best[0], 4), best[1]
    if marker_clash:
        return False, 0.0, f"marker mismatch ({marker_clash})"
    if allow_subtitle:
        rec_raws = [str(record_title or "")]
        m = _TRAILING_BRACKET_RE.search(rec_raws[0])
        if m and m.start() > 0 and not _is_blocked_text(_tokens(m.group(0))):
            rec_raws.append(rec_raws[0][:m.start()])
        for rr in rec_raws:
            for cr in cand_raws:
                if _subtitle_relation(rr, cr) or _subtitle_relation(cr, rr):
                    return True, 0.9, "subtitle"
    return False, 0.0, "title differs"


def titles_match(record_title, other_title, allow_subtitle: bool = True):
    """`title_match` for two plain title strings (e.g. a record title vs the title an API returned)."""
    return title_match(record_title, Candidate(doi="", title=str(other_title or "")), allow_subtitle)


def _is_main_part_of(short_raw, long_raw) -> bool:
    """True when `short_raw` equals the leading segment(s) of `long_raw` (its main title).

    Crossref keeps the subtitle in a separate field, so the title an API returns for a DOI is
    often only the main title of the record's full 'Main: Subtitle' string. Needs >= 2
    content tokens in the main part; the omitted rest must not look like a series designator
    or carry a marker word (erratum, reply, ...).
    """
    st = _content(_tokens(short_raw))
    if len(st) < 2:
        return False
    segs = [p for p in _SEGMENT_SPLIT_RE.split(_strip_markup(long_raw)) if p and p.strip()]
    short_markers = _markers(_tokens(short_raw))
    for k in range(1, len(segs)):
        head, tail = " ".join(segs[:k]), " ".join(segs[k:])
        ht = _content(_tokens(head))
        if len(ht) < 2:
            continue
        ok, _ = _aligned(st, ht)
        if ok and _markers(_tokens(head)) == short_markers and _omission_ok(_tokens(tail)):
            return True
    return False


def same_work_titles(record_title, api_title):
    """Do a record title and the title an API returned for its DOI name the same work?

    `titles_match`, plus: one of the two may be just the main title of the other
    ('Deep learning' vs 'Deep learning: methods and applications'). Used for the re-check
    after a DOI has been fetched; the DOI itself was already chosen by `lookup_doi`.
    """
    ok, score, why = titles_match(record_title, api_title)
    if ok:
        return ok, score, why
    if _is_main_part_of(api_title, record_title) or _is_main_part_of(record_title, api_title):
        return True, 0.9, "main title"
    return False, 0.0, why


def _is_generic_title(record_title) -> bool:
    toks = _tokens(record_title)
    return len(_content(toks)) < 5 or " ".join(toks) in _GENERIC_TITLES


# ── Record + decision objects ───────────────────────────────────────────────────
@dataclass
class DoiDecision:
    """Outcome of one lookup. `doi` is set only when outcome == 'filled'."""
    doi: str | None
    outcome: str             # filled | ambiguous | preprint_only | duplicate | type_rejected | no_match | api_error
    source: str = ""         # crossref | openalex
    cand_title: str = ""
    cand_type: str = ""
    score: float = 0.0
    reason: str = ""
    preprint_doi: str | None = None
    duplicate_of: str | None = None
    cand_container: str = ""
    warning: str = ""


class DoiLookupState:
    """Per-run state: query cache (never shared across runs) + API circuit breaker."""

    def __init__(self):
        self.cache: dict = {}            # exact request -> (data, status)
        self.exhausted: set = set()      # sources that hit a hard quota this run
        self.blocked_until: dict = {}    # source -> time.monotonic() deadline (short Retry-After)
        self.fail_streak: dict = {}      # source -> consecutive failed requests
        self.http_calls = 0
        self.api_errors = 0


@dataclass
class _Rec:
    uid: str
    title: str
    year: int | None
    authors_raw: str
    authors: list
    containers: list
    issns: set
    volume: str
    first_page: str
    preprint: bool
    expected_types: frozenset = dc_field(default_factory=frozenset)   # from DT, see _expected_types


def _parse_year(v):
    s = _s(v)
    if not s:
        return None
    try:
        y = int(float(s))
        return y if 1000 <= y <= 2200 else None
    except (TypeError, ValueError):
        m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", s)
        return int(m.group(1)) if m else None


def _build_rec(record: dict) -> _Rec:
    g = lambda k: _s(record.get(k))          # noqa: E731
    au = g("AU") or g("AF")
    containers = [x for x in (g("SO"), g("JI"), g("J9")) if x]
    page = g("BP").split("-")[0].strip().lower()
    return _Rec(
        uid=g("UID"),
        title=g("TI"),
        year=_parse_year(record.get("PY")),
        authors_raw=au,
        authors=parse_record_authors(au, 3),
        containers=containers,
        issns=_issn_set(g("SN"), g("EI")),
        volume=g("VL").lstrip("0").lower(),
        first_page=page.lstrip("0"),
        preprint=any(_PREPRINT_SOURCE_RE.search(c.casefold()) for c in containers),
        expected_types=_expected_types(g("DT")),
    )


@dataclass
class _Eval:
    cand: Candidate
    ok: bool = False
    blocked_by_type: bool = False    # passed every check except the type policy
    score: float = 0.0
    why: str = ""


def _evaluate(rec: _Rec, cand: Candidate) -> _Eval:
    """Run the type/year/ISSN/author/title checks of one candidate (see module comment)."""
    ev = _Eval(cand=cand)
    type_ok = cand.type in _ALLOWED_TYPES

    if not cand.years:
        ev.why = "candidate has no year"
        return ev
    if rec.year is not None and not any(abs(y - rec.year) <= 1 for y in cand.years):
        ev.why = f"year mismatch (record {rec.year}, candidate {sorted(cand.years)})"
        return ev

    issn_match = False
    if rec.issns and cand.issn:
        if rec.issns & cand.issn:
            issn_match = True
        else:
            ev.why = "ISSN conflict"
            return ev
    csim = _container_sim(rec.containers, cand.container) if (rec.containers and cand.container) else 0.0
    strong = issn_match or csim >= 0.9

    have_rec_au, have_cand_au = bool(rec.authors), bool(cand.authors)
    agree = False
    if have_rec_au and have_cand_au:
        agree = _authors_agree(rec.authors, cand)
        if not agree:
            ev.why = "authors differ"
            return ev
    if not have_rec_au and not have_cand_au:
        ev.why = "no authors on either side"
        return ev

    # Subtitle omission is only allowed with positive author AND year agreement.
    allow_sub = agree and rec.year is not None
    ok, tscore, why = title_match(rec.title, cand, allow_subtitle=allow_sub)
    if not ok:
        ev.why = f"title: {why}"
        return ev

    if _is_generic_title(rec.title) and not (agree and strong):
        ev.why = "generic/short title needs author agreement and ISSN/container match"
        return ev
    if have_rec_au and not have_cand_au and not strong:
        ev.why = "candidate has no authors and no ISSN/container match"
        return ev
    if not have_rec_au and not strong:
        ev.why = "record has no authors and no ISSN/container match"
        return ev
    if rec.year is None and not (agree and strong):
        ev.why = "record year unknown: needs author agreement and ISSN/container match"
        return ev

    score = tscore
    if issn_match:
        score += 0.03
    if csim >= 0.9:
        score += 0.02
    elif rec.containers and cand.container and csim < 0.5 and not issn_match:
        score -= 0.03                      # penalty only, never a rejection
    if rec.volume and cand.volume and rec.volume == cand.volume.lstrip("0").lower():
        score += 0.01
    cpage = cand.page.split("-")[0].strip().lower().lstrip("0")
    if rec.first_page and cpage and rec.first_page == cpage:
        score += 0.01
    ev.score = round(score, 4)

    bits = [f"title {why}", f"year {rec.year if rec.year is not None else '?'}~{sorted(cand.years)}"]
    bits.append("authors agree" if agree else "authors n/a")
    if issn_match:
        bits.append("ISSN match")
    elif csim:
        bits.append(f"container {csim:.2f}")
    if _SUSPICIOUS_DOI_RE.search(cand.doi):
        bits.append("signal: review/supplement-like DOI")
    ev.why = "; ".join(bits)

    if not type_ok:
        ev.blocked_by_type = True
        ev.why = f"type '{cand.type}' not allowed ({ev.why})"
        return ev
    ev.ok = True
    return ev


# ── HTTP with a per-run cache and a circuit breaker ─────────────────────────────
def _fetch_json(url: str, params: dict, headers, state: DoiLookupState, source: str):
    """GET -> (data | None, status) with status in ok | not_found | error | exhausted.

    Never sleeps longer than MAX_RETRY_AFTER: a 429 with a long Retry-After marks the source
    exhausted (hard quota, > HARD_QUOTA_SECONDS) or blocks it for that long, instead of
    sleeping and silently returning nothing.
    """
    if source in state.exhausted:
        return None, "exhausted"
    if time.monotonic() < state.blocked_until.get(source, 0.0):
        return None, "error"
    key = (url, tuple(sorted((params or {}).items())))
    if key in state.cache:
        return state.cache[key]
    result = (None, "error")
    for attempt in range(2):
        try:
            r = _http_get(url, params, headers)
        except Exception:
            if attempt == 0:          # one retry for a transient network error
                time.sleep(1.0)
                continue
            state.api_errors += 1
            break
        state.http_calls += 1
        code = getattr(r, "status_code", 0)
        if source == "openalex":
            remaining = _hdr(r, "X-RateLimit-Remaining")
            try:
                if remaining is not None and float(remaining) < OPENALEX_MIN_CREDITS:
                    state.exhausted.add("openalex")
            except (TypeError, ValueError):
                pass
        if code in (429, 503):
            wait = _retry_after(r)
            wait = 1.0 if wait is None else wait
            if code == 429 and wait > HARD_QUOTA_SECONDS:
                state.exhausted.add(source)
                state.api_errors += 1
                return None, "exhausted"
            if wait > MAX_RETRY_AFTER:
                state.blocked_until[source] = time.monotonic() + wait
                state.api_errors += 1
                break
            if attempt == 0:
                time.sleep(wait)
                continue
            state.api_errors += 1
            break
        if code == 404:
            result = (None, "not_found")
            break
        if not (200 <= code < 300):
            state.api_errors += 1
            break
        try:
            result = (r.json(), "ok")
        except Exception:
            state.api_errors += 1
        break
    if result[1] == "error":
        # A source that keeps failing (offline, outage) must not cost seconds per record for
        # the rest of the run: after a streak of failures it is given up for this run.
        streak = state.fail_streak[source] = state.fail_streak.get(source, 0) + 1
        if streak >= MAX_FAIL_STREAK:
            state.exhausted.add(source)
    else:
        state.fail_streak[source] = 0
    if result[1] in ("ok", "not_found"):
        if len(state.cache) >= CACHE_MAX_ENTRIES:
            state.cache.pop(next(iter(state.cache)))      # oldest first: the cache stays bounded
        state.cache[key] = result
    return result


# ── Query construction ──────────────────────────────────────────────────────────
def _clean_query_title(title) -> str:
    s = _strip_markup(title).replace("\ufffd", "")
    return re.sub(r"\s+", " ", s).strip()[:300]


def _crossref_query(title, authors, year) -> dict:
    """Crossref /works request parameters for a record (without `mailto`).

    query.bibliographic = '<clean title> <first author surname> <year>'; surname and year
    are omitted when unknown.
    """
    parts = [_clean_query_title(title)]
    first = parse_record_authors(authors, 1)
    if first and first[0].family:
        parts.append(first[0].family)
    y = _parse_year(year)
    if y:
        parts.append(str(y))
    return {
        "query.bibliographic": " ".join(p for p in parts if p).strip(),
        "rows": CROSSREF_ROWS,
        "select": CROSSREF_SELECT,
    }


def _crossref_headers(email) -> dict:
    return {"User-Agent": f"BibexPy (mailto:{email})"} if email else {}


# ── Decision ────────────────────────────────────────────────────────────────────
def _holder_of(existing, key: str) -> str:
    getter = getattr(existing, "get", None)
    return str(getter(key) or "") if callable(getter) else ""


def _class_of(ev: _Eval, rec: _Rec) -> int:
    if ev.cand.is_preprint:
        return -1 if rec.preprint else _PREPRINT_CLASS
    return _TYPE_CLASS.get(ev.cand.type, 3)


# Small bonus when the candidate's type is the one the record's document type implies.
TYPE_AFFINITY_BONUS = 0.015


def _expected_types(doc_type: str) -> frozenset:
    """Crossref types implied by a WoS/Scopus document type ('Book Chapter' -> book-chapter, ...)."""
    d = _norm_text(doc_type)
    out = set()
    if "book chapter" in d:
        out.add("book-chapter")
    if "proceedings" in d or "conference" in d:
        out.add("proceedings-article")
    if re.search(r"\b(article|review|letter|note|short survey)\b", d):
        out.add("journal-article")
    return frozenset(out)


def _rank_score(ev: _Eval, rec: _Rec) -> float:
    """Evidence score used for ranking: the match score plus the document-type affinity bonus."""
    bonus = TYPE_AFFINITY_BONUS if ev.cand.type in rec.expected_types else 0.0
    return round(ev.score + bonus, 4)


def _decision_from(ev: _Eval, outcome: str, **extra) -> DoiDecision:
    c = ev.cand
    return DoiDecision(
        doi=c.doi if outcome == "filled" else None,
        outcome=outcome,
        source=c.source,
        cand_title=(c.title + (": " + c.subtitle if c.subtitle else "")).strip(),
        cand_type=c.type,
        score=ev.score,
        reason=ev.why,
        cand_container=c.container[0] if c.container else "",
        **extra,
    )


def _decide(rec: _Rec, cands: list, state: DoiLookupState, existing, email, follow: bool) -> DoiDecision:
    seen, evals = set(), []
    for c in cands:
        k = doi_key(c.doi)
        if k and k not in seen:
            seen.add(k)
            evals.append(_evaluate(rec, c))
    passing = [e for e in evals if e.ok]
    # A repository copy (Zenodo, figshare, ...) is never the paper's DOI, even for a record
    # that itself comes from a preprint server.
    pool = [e for e in passing if not e.cand.is_preprint or (rec.preprint and not e.cand.is_repository)]
    preprints = [e for e in passing if e.cand.is_preprint]
    followed_from = {}

    if not pool and preprints:
        # Only a preprint (for a preprint record: only a repository copy) matched: look for its
        # published version (Crossref relation).
        fetched = 0
        for pe in sorted(preprints, key=lambda e: -e.score):
            targets = []
            for rel in (pe.cand.relation.get("is-preprint-of") or []):
                if isinstance(rel, dict) and str(rel.get("id-type") or "doi").lower() == "doi":
                    t = clean_doi(rel.get("id"))
                    if t and doi_key(t) not in seen:
                        targets.append(t)
            for target in targets:
                if not follow or fetched >= 2:
                    break
                fetched += 1
                params = {"mailto": email} if email else {}
                data, status = _fetch_json(f"{CROSSREF_WORKS_URL}/{quote(target, safe='/')}",
                                           params, _crossref_headers(email), state, "crossref")
                item = (data or {}).get("message") if isinstance(data, dict) else None
                vor = _candidate_from_crossref(item, 0) if isinstance(item, dict) else None
                if vor is None:
                    continue
                seen.add(doi_key(vor.doi))
                ev = _evaluate(rec, vor)
                if ev.ok and not vor.is_preprint:
                    ev.why = f"published version of preprint {pe.cand.doi}; {ev.why}"
                    pool.append(ev)
                    followed_from[doi_key(vor.doi)] = pe.cand.doi
        if not pool:
            best = max(preprints, key=lambda e: e.score)
            return _decision_from(
                best, "preprint_only", preprint_doi=best.cand.doi,
                warning=("repository copy DOI - do not use as the publisher DOI" if best.cand.is_repository
                         else "preprint DOI - do not use as the journal DOI"),
            )

    if not pool:
        blocked = [e for e in evals if e.blocked_by_type]
        if blocked:
            return _decision_from(max(blocked, key=lambda e: e.score), "type_rejected")
        if not cands:
            return DoiDecision(None, "no_match", reason="no candidates returned")
        top = min(evals, key=lambda e: e.cand.rank)
        return DoiDecision(None, "no_match",
                           reason=f"no acceptable candidate among {len(evals)} (top result: {top.why})")

    # Rank by evidence, not by publication class: a materially better match of another type
    # (the exact book chapter vs a journal article with a longer title) must win. A record that
    # is itself a preprint prefers preprint candidates (they are the only ones in the pool then).
    preprint_pool = [e for e in pool if e.cand.is_preprint]
    ranked_pool = preprint_pool or pool
    tier = sorted(ranked_pool, key=lambda e: (-_rank_score(e, rec), _class_of(e, rec), e.cand.rank))
    top = tier[0]
    # Two different DOIs within SCORE_MARGIN at the top -> abstain, whatever their classes.
    rivals = [e for e in tier[1:] if doi_key(e.cand.doi) != doi_key(top.cand.doi)
              and _rank_score(top, rec) - _rank_score(e, rec) <= SCORE_MARGIN + 1e-9]
    if rivals:
        dec = _decision_from(top, "ambiguous")
        dec.reason = (f"{len(rivals) + 1} different DOIs within {SCORE_MARGIN} score: "
                      + ", ".join([top.cand.doi] + [e.cand.doi for e in rivals[:3]]))
        return dec

    key = doi_key(top.cand.doi)
    pre = followed_from.get(key)
    if key in existing:
        return _decision_from(
            top, "duplicate", duplicate_of=_holder_of(existing, key) or None, preprint_doi=pre,
            warning="possible duplicate Smart Merge did not merge",
        )
    return _decision_from(top, "filled", preprint_doi=pre)


# How informative an outcome is (lower wins) when Crossref and the OpenAlex fallback disagree.
_OUTCOME_RANK = {"filled": 0, "duplicate": 1, "ambiguous": 1, "preprint_only": 2,
                 "type_rejected": 3, "api_error": 4, "no_match": 5}


def lookup_doi(record: dict, *, existing=(), email=None, openalex_key=None, state=None) -> DoiDecision:
    """Find the DOI of a record that lacks one — or abstain.

    `record` keys: TI, AU, AF, PY, SO, JI, J9, SN, EI, VL, BP, DT, UID (missing ones are fine).
    `existing` holds `doi_key()` values already used by other rows or assigned earlier in
    this run (a dict {key: uid} also names the holder). `state` is a DoiLookupState shared
    across the records of ONE run (cache + circuit breaker); a fresh one is used when omitted.

    Crossref is queried first; OpenAlex is only a fallback when Crossref produced no
    acceptable candidate and no ambiguity.
    """
    state = state if state is not None else DoiLookupState()
    rec = _build_rec(record)
    if not rec.title:
        return DoiDecision(None, "no_match", reason="empty title")

    params = _crossref_query(rec.title, rec.authors_raw, rec.year)
    if email:
        params["mailto"] = email
    data, status = _fetch_json(CROSSREF_WORKS_URL, params, _crossref_headers(email), state, "crossref")
    if status == "ok":
        message = data.get("message") if isinstance(data, dict) else None
        items = (message.get("items") if isinstance(message, dict) else None) or []
        cands = [c for c in (_candidate_from_crossref(it, i) for i, it in enumerate(items) if isinstance(it, dict)) if c]
        dec = _decide(rec, cands, state, existing, email, follow=True)
    else:
        dec = DoiDecision(None, "api_error", reason=f"crossref {status}")

    if dec.outcome in ("no_match", "type_rejected", "preprint_only", "api_error"):
        fallback = _lookup_openalex(rec, email, openalex_key, state, existing)
        if fallback is not None:
            # The more informative outcome wins; a plain "no match" never replaces anything.
            if _OUTCOME_RANK.get(fallback.outcome, 9) < _OUTCOME_RANK.get(dec.outcome, 9):
                fallback.reason = f"{dec.reason}; {fallback.reason}" if dec.reason else fallback.reason
                return fallback
        if dec.outcome == "no_match" and "openalex" in state.exhausted:
            dec.reason += "; openalex skipped (quota exhausted)"
    return dec


def _lookup_openalex(rec: _Rec, email, key, state: DoiLookupState, existing):
    """OpenAlex fallback search; None when the source is exhausted or skipped."""
    if "openalex" in state.exhausted:
        return None
    params = {"search": _clean_query_title(rec.title), "per_page": OPENALEX_ROWS, "select": OPENALEX_SELECT}
    if rec.year is not None:
        params["filter"] = f"publication_year:{rec.year - 1}|{rec.year}|{rec.year + 1}"
    if key:
        params["api_key"] = key
    elif email:
        params["mailto"] = email
    data, status = _fetch_json(OPENALEX_WORKS_URL, params, None, state, "openalex")
    if status == "exhausted":
        return DoiDecision(None, "no_match", reason="openalex exhausted")
    if status != "ok":
        return DoiDecision(None, "api_error", reason=f"openalex {status}")
    results = (data.get("results") if isinstance(data, dict) else None) or []
    cands = [c for c in (_candidate_from_openalex(w, i) for i, w in enumerate(results) if isinstance(w, dict)) if c]
    return _decide(rec, cands, state, existing, email, follow=False)


def resolve_doi(title, authors=None, year=None, *, crossref_email: str | None = None):
    """Backwards-compatible wrapper around lookup_doi: the DOI, or None when unsure.

    Nothing is cached across calls; use lookup_doi with a shared DoiLookupState to process
    many records.
    """
    if not title or not str(title).strip():
        return None
    dec = lookup_doi({"TI": title, "AU": authors, "PY": year}, email=crossref_email or None)
    return dec.doi


def _decode_inverted_abstract(inv: dict) -> str:
    """OpenAlex abstract_inverted_index ({token: [positions]}) → plain text."""
    if not isinstance(inv, dict) or not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for token, idxs in inv.items():
        if isinstance(idxs, list):
            for p in idxs:
                positions.append((int(p), token))
    positions.sort(key=lambda x: x[0])
    return " ".join(tok for _, tok in positions)

def load_api_config() -> dict:
    """Load API configuration from API_config.json.

    Looks first at BIBEXPY_CONFIG_DIR (package mode, e.g. ~/.bibexpy), then at the
    source-tree location. Silently returns an empty dict if nothing is found — on the
    v2 server the API keys come from env/config.py, so this file is not required.
    """
    candidates = []
    cfg_dir = os.environ.get("BIBEXPY_CONFIG_DIR")
    if cfg_dir:
        candidates.append(os.path.join(os.path.expanduser(cfg_dir), 'API_config.json'))
    candidates.append(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'API_config.json')
    )
    config_path = next((p for p in candidates if os.path.exists(p)), None)
    if not config_path:
        return {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"\nError loading API configuration: {str(e)}")
        return {}

def get_api_credential(service: str, field: str = 'api_key') -> str:
    """Get API credential for a specific service"""
    config = load_api_config()
    if service in config and field in config[service]:
        return config[service][field]
    return None

def _cache_successful(maxsize: int = 4096):
    """Memoize a function, remembering only NON-EMPTY results (unlike `lru_cache`).

    The metadata extractors return {} for every failure (429/5xx, timeout, bad key). Caching
    that would make one transient hiccup stick for the whole process lifetime and silently
    switch off the "second insurance" re-check of freshly looked-up DOIs; a failed call is
    simply repeated next time. Oldest entries are evicted first. `cache_clear()` and
    `cache_info()` are kept for callers that used the `lru_cache` API.
    """
    def deco(fn):
        store: OrderedDict = OrderedDict()
        lock = threading.Lock()
        stats = {"hits": 0, "misses": 0}

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            with lock:
                if key in store:
                    store.move_to_end(key)
                    stats["hits"] += 1
                    return store[key]
                stats["misses"] += 1
            value = fn(*args, **kwargs)
            if value:                                   # never remember {} / None (a failure)
                with lock:
                    store[key] = value
                    while len(store) > maxsize:
                        store.popitem(last=False)
            return value

        def cache_clear():
            with lock:
                store.clear()
                stats["hits"] = stats["misses"] = 0

        wrapper.cache_clear = cache_clear
        wrapper.cache_info = lambda: (stats["hits"], stats["misses"], maxsize, len(store))
        return wrapper
    return deco


@_cache_successful(4096)
def extract_metadata_from_crossref(doi: str, email: str = None) -> dict:
    """Extract metadata from CrossRef API (Free, no key required)"""
    try:
        url = f"https://api.crossref.org/works/{doi}"
        headers = {
            'User-Agent': f'BibexPy/2.0 (mailto:{email or "info@bibexpy.org"})'
        }
        response = _get_with_retry(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            work = data['message']
            
            # Extract relevant fields
            metadata = {}
            
            # DOI
            metadata['DI'] = work.get('DOI', '')
            
            # Document Type
            if 'type' in work:
                metadata['DT'] = work['type']
            
            # Authors (+ ORCID)
            if 'author' in work:
                authors = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in work['author']]
                metadata['AU'] = '; '.join(authors)
                # ORCID (OI) — often empty in CrossRef, but a definitive signal when present.
                orcids = []
                for a in work['author']:
                    oid = str(a.get('ORCID') or "").replace("https://orcid.org/", "").replace("http://orcid.org/", "").strip()
                    if oid:
                        nm = f"{a.get('given', '')} {a.get('family', '')}".strip()
                        orcids.append(f"{nm}/{oid}")
                if orcids:
                    metadata['OI'] = '; '.join(orcids)
            
            # Title
            if 'title' in work:
                metadata['TI'] = work['title'][0]
            
            # Publication Year
            if 'issued' in work and 'date-parts' in work['issued']:
                metadata['PY'] = work['issued']['date-parts'][0][0]
            
            # Journal/Container Title
            if 'container-title' in work:
                metadata['SO'] = work['container-title'][0]
            
            # Publisher
            if 'publisher' in work:
                metadata['PU'] = work['publisher']
            
            # Publisher Address/Location
            if 'publisher-location' in work:
                metadata['PA'] = work['publisher-location']
            
            # Abstract
            if 'abstract' in work:
                metadata['AB'] = work['abstract']
            
            # ISSN
            if 'ISSN' in work:
                metadata['SN'] = '; '.join(work['ISSN'])
            
            # URL
            if 'URL' in work:
                metadata['UR'] = work['URL']
            
            # Subject -> keywords (DE) only. WC/SC are NOT real WoS/Scopus
            # categories; ML enrichment fills those in (de-collapse).
            if 'subject' in work and work['subject']:
                metadata['DE'] = '; '.join(work['subject'])
            
            # License
            if 'license' in work and work['license']:
                license_urls = [lic.get('URL', '') for lic in work['license']]
                metadata['LI'] = '; '.join(filter(None, license_urls))
            
            return metadata
    except Exception as e:
        print(f"CrossRef API Error: {str(e)}")
    return {}

def truncate_url(url: str, max_length: int = 2079) -> str:
    """Truncate the URL to fit Excel's URL character limit"""
    if not url or len(url) <= max_length:
        return url
    return url[:max_length-3] + "..."

@_cache_successful(4096)
def extract_metadata_from_openalex(doi: str, email: str = None, api_key: str = None) -> dict:
    """Extract metadata from OpenAlex API (a singleton /works/doi: lookup, which is free).

    With `api_key` the key is sent as a query parameter; otherwise `mailto` is used.
    """
    try:
        url = f"https://api.openalex.org/works/doi:{doi}"
        params = {"api_key": api_key} if api_key else ({"mailto": email} if email else None)
        response = _get_with_retry(url, params=params)
        
        if response.status_code == 200:
            work = response.json()
            
            # Extract relevant fields
            metadata = {}
            
            # DOI
            if 'doi' in work:
                metadata['DI'] = work['doi']
            
            # Document Type
            if 'type' in work:
                metadata['DT'] = work['type']
            
            # Title
            if 'title' in work:
                metadata['TI'] = work['title']
            elif 'display_name' in work:
                metadata['TI'] = work['display_name']
            
            # Authors and Institutions
            if 'authorships' in work:
                # Authors
                authors = []
                institutions = []
                author_institutions = []  # For author-institution pairs
                orcids = []          # WoS OI: 'Author/0000-0000-0000-0000'
                researcher_ids = []  # WoS RI: 'Author/<OpenAlex A-ID>' (fallback identifier)
                rors = []            # unique ROR institution URIs
                countries = []       # ISO country codes (from author institutions)

                for authorship in work['authorships']:
                    if 'author' in authorship and 'display_name' in authorship['author']:
                        author = authorship['author']
                        author_name = author['display_name']
                        authors.append(author_name)

                        # ORCID (OI) — the gold standard for disambiguation
                        orcid = author.get('orcid')
                        if orcid:
                            oid = str(orcid).replace("https://orcid.org/", "").strip()
                            if oid:
                                orcids.append(f"{author_name}/{oid}")
                        # Author identifier (RI fallback) — OpenAlex A-ID
                        aid = author.get('id')
                        if aid:
                            researcher_ids.append(f"{author_name}/{str(aid).replace('https://openalex.org/', '').strip()}")

                        # Get institutions for this author (+ ROR + country)
                        if 'institutions' in authorship:
                            author_insts = []
                            for inst in authorship['institutions']:
                                if 'display_name' in inst:
                                    inst_name = inst['display_name']
                                    institutions.append(inst_name)
                                    author_insts.append(inst_name)
                                if inst.get('ror'):
                                    rors.append(str(inst['ror']).replace("https://ror.org/", "").strip())
                                if inst.get('country_code'):
                                    countries.append(str(inst['country_code']).strip().upper())

                            if author_insts:
                                author_institutions.append(f"{author_name} [{'; '.join(author_insts)}]")
                        # Author-level countries[] (may be present even when there is no institution)
                        for cc in authorship.get('countries', []) or []:
                            if cc:
                                countries.append(str(cc).strip().upper())

                if authors:
                    metadata['AU'] = '; '.join(authors)
                # Unique institutions
                if institutions:
                    metadata['C1'] = '; '.join(list(dict.fromkeys(institutions)))
                # Author-Institution pairs
                if author_institutions:
                    metadata['AF'] = '; '.join(author_institutions)
                # ORCID (OI) + ResearcherID (RI) + ROR + country codes
                if orcids:
                    metadata['OI'] = '; '.join(orcids)
                if researcher_ids:
                    metadata['RI'] = '; '.join(researcher_ids)
                if rors:
                    metadata['ROR'] = '; '.join(dict.fromkeys(rors))
                if countries:
                    metadata['CC'] = '; '.join(dict.fromkeys(countries))

            # Publication Year
            if 'publication_year' in work:
                metadata['PY'] = work['publication_year']
            
            # Journal/Publisher — new schema primary_location.source (host_venue is deprecated).
            venue = {}
            ploc = work.get('primary_location') or {}
            if isinstance(ploc, dict) and isinstance(ploc.get('source'), dict):
                venue = ploc['source']
            elif isinstance(work.get('host_venue'), dict):
                venue = work['host_venue']
            if venue:
                if venue.get('display_name'):
                    metadata['SO'] = venue['display_name']
                if venue.get('host_organization_name') or venue.get('publisher'):
                    metadata['PU'] = venue.get('host_organization_name') or venue.get('publisher')
                if venue.get('issn_l'):
                    metadata['SN'] = venue['issn_l']
            if isinstance(ploc, dict) and ploc.get('landing_page_url'):
                metadata['UR'] = truncate_url(ploc['landing_page_url'])

            # Abstract — OpenAlex provides 'abstract_inverted_index'; there is no plain 'abstract'.
            if work.get('abstract'):
                metadata['AB'] = work['abstract']
            elif work.get('abstract_inverted_index'):
                ab = _decode_inverted_abstract(work['abstract_inverted_index'])
                if ab:
                    metadata['AB'] = ab

            # Concepts -> DE (keywords).
            if 'concepts' in work:
                concepts = [c['display_name'] for c in work['concepts'] if 'display_name' in c]
                if concepts:
                    metadata['DE'] = '; '.join(concepts)

            # Topics -> WC (subfield, more specific) + SC (field, broader). This is the
            # OpenAlex topic classification; it does not map one-to-one onto the WoS
            # taxonomy but is open/transparent. It kicks in for records where both WC and
            # SC are empty (first-non-empty-wins).
            topics = work.get('topics') or ([work['primary_topic']] if work.get('primary_topic') else [])
            if topics:
                subfields, fields = [], []
                for tp in topics:
                    if not isinstance(tp, dict):
                        continue
                    sf = (tp.get('subfield') or {}).get('display_name')
                    fd = (tp.get('field') or {}).get('display_name')
                    if sf:
                        subfields.append(sf)
                    if fd:
                        fields.append(fd)
                if subfields:
                    metadata['WC'] = '; '.join(dict.fromkeys(subfields))
                if fields:
                    metadata['SC'] = '; '.join(dict.fromkeys(fields))
            
            # Citations
            if 'cited_by_count' in work:
                metadata['TC'] = work['cited_by_count']
            
            # Open Access Information
            if 'open_access' in work:
                oa_info = work['open_access']
                oa_details = []
                if 'is_oa' in oa_info:
                    oa_details.append(f"is_oa: {oa_info['is_oa']}")
                if 'oa_status' in oa_info:
                    oa_details.append(f"status: {oa_info['oa_status']}")
                if 'oa_url' in oa_info:
                    oa_details.append(f"url: {oa_info['oa_url']}")
                if oa_details:
                    metadata['OA'] = '; '.join(oa_details)
            
            # NOTE: OpenAlex 'referenced_works' is NOT written to CR — this field
            # returns a list of openalex.org/W... URLs; it cannot be converted to the
            # WoS citation grammar (AUTHOR, YEAR, SOURCE, Vn, Pn) and would produce data
            # that is unusable for VOSviewer/bibliometrix.

            return metadata
    except Exception as e:
        print(f"OpenAlex API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_scopus(doi: str, api_key: str) -> dict:
    """Extract metadata from Scopus API (Requires API key)"""
    try:
        url = f"https://api.elsevier.com/content/abstract/doi/{doi}"
        headers = {
            'X-ELS-APIKey': api_key,
            'Accept': 'application/json'
        }
        response = _get_with_retry(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'abstracts-retrieval-response' in data:
                work = data['abstracts-retrieval-response']
                metadata = {}
                
                # Check coredata
                if 'coredata' in work:
                    coredata = work['coredata']
                    
                    # DOI
                    if 'prism:doi' in coredata:
                        metadata['DI'] = coredata['prism:doi']
                    elif 'dc:identifier' in coredata:
                        metadata['DI'] = coredata['dc:identifier']
                    
                    # Document Type
                    if 'prism:aggregationType' in coredata:
                        metadata['DT'] = coredata['prism:aggregationType']
                    
                    # Title
                    if 'dc:title' in coredata:
                        metadata['TI'] = coredata['dc:title']
                    
                    # Journal Name
                    if 'prism:publicationName' in coredata:
                        metadata['SO'] = coredata['prism:publicationName']
                    
                    # Publisher
                    if 'dc:publisher' in coredata:
                        metadata['PU'] = coredata['dc:publisher']
                    
                    # Volume
                    if 'prism:volume' in coredata:
                        metadata['VL'] = coredata['prism:volume']
                    
                    # Issue
                    if 'prism:issueIdentifier' in coredata:
                        metadata['IS'] = coredata['prism:issueIdentifier']
                    
                    # Page Numbers
                    if 'prism:startingPage' in coredata:
                        metadata['BP'] = coredata['prism:startingPage']
                    if 'prism:endingPage' in coredata:
                        metadata['EP'] = coredata['prism:endingPage']
                    
                    # Publication Year
                    if 'prism:coverDate' in coredata:
                        metadata['PY'] = coredata['prism:coverDate'][:4]
                    
                    # ISSN
                    if 'prism:issn' in coredata:
                        metadata['SN'] = coredata['prism:issn']
                    
                    # Abstract
                    if 'dc:description' in coredata:
                        metadata['AB'] = coredata['dc:description']
                    
                    # URLs
                    if 'link' in coredata:
                        urls = []
                        for link in coredata['link']:
                            if '@href' in link:
                                urls.append(link['@href'])
                        if urls:
                            metadata['UR'] = '; '.join(urls)
                    
                    # Citation Count
                    if 'citedby-count' in coredata:
                        metadata['TC'] = coredata['citedby-count']
                
                # Authors and Affiliations
                if 'authors' in work:
                    authors = []
                    affiliations = []
                    author_affiliations = []
                    orcids = []  # WoS OI

                    if 'author' in work['authors']:
                        for author in work['authors']['author']:
                            author_name = []
                            if 'ce:given-name' in author:
                                author_name.append(author['ce:given-name'])
                            if 'ce:surname' in author:
                                author_name.append(author['ce:surname'])

                            if author_name:
                                full_name = ' '.join(author_name)
                                authors.append(full_name)
                                # ORCID (Scopus '@orcid')
                                oid = str(author.get('@orcid') or "").strip()
                                if oid:
                                    orcids.append(f"{full_name}/{oid}")

                                # Author affiliations
                                if 'affiliation' in author:
                                    author_affs = []
                                    if isinstance(author['affiliation'], list):
                                        for aff in author['affiliation']:
                                            if 'affilname' in aff:
                                                affiliations.append(aff['affilname'])
                                                author_affs.append(aff['affilname'])
                                    else:
                                        aff = author['affiliation']
                                        if 'affilname' in aff:
                                            affiliations.append(aff['affilname'])
                                            author_affs.append(aff['affilname'])
                                    
                                    if author_affs:
                                        author_affiliations.append(f"{full_name} [{'; '.join(author_affs)}]")
                    
                    if authors:
                        metadata['AU'] = '; '.join(authors)
                    if affiliations:
                        metadata['C1'] = '; '.join(list(dict.fromkeys(affiliations)))
                    if author_affiliations:
                        metadata['AF'] = '; '.join(author_affiliations)
                    if orcids:
                        metadata['OI'] = '; '.join(orcids)

                # Subject Areas (ASJC) -> DE + WC/SC. This is reached only if the Scopus key is active.
                # NOTE: when there is a single area, Elsevier returns 'subject-area' as a SINGLE dict instead of a list.
                sa = (work.get('subject-areas') or {}).get('subject-area')
                if isinstance(sa, dict):
                    sa = [sa]
                if isinstance(sa, list):
                    subjects = [s['$'] for s in sa if isinstance(s, dict) and '$' in s]
                    if subjects:
                        uniq = '; '.join(dict.fromkeys(subjects))
                        metadata['DE'] = uniq
                        metadata['WC'] = uniq
                        metadata['SC'] = uniq
                
                return metadata
    except Exception as e:
        print(f"Scopus API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_datacite(doi: str) -> dict:
    """Extract metadata from DataCite API (Free, no key required)"""
    try:
        url = f"https://api.datacite.org/dois/{doi}"
        headers = {
            'Accept': 'application/vnd.api+json'
        }
        response = _get_with_retry(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'attributes' in data['data']:
                work = data['data']['attributes']
                
                # Extract relevant fields
                metadata = {}
                
                # Authors
                if 'creators' in work:
                    authors = []
                    for creator in work['creators']:
                        if isinstance(creator, dict):
                            name_parts = []
                            if 'givenName' in creator:
                                name_parts.append(creator['givenName'])
                            if 'familyName' in creator:
                                name_parts.append(creator['familyName'])
                            if name_parts:
                                authors.append(' '.join(name_parts))
                            elif 'name' in creator:
                                authors.append(creator['name'])
                    if authors:
                        metadata['AU'] = '; '.join(authors)
                
                # Title
                if 'titles' in work and work['titles']:
                    metadata['TI'] = work['titles'][0].get('title', '')
                
                # Publication Year
                if 'publicationYear' in work:
                    metadata['PY'] = work['publicationYear']
                
                # Document Type
                if 'types' in work and 'resourceTypeGeneral' in work['types']:
                    metadata['DT'] = work['types']['resourceTypeGeneral']
                
                # Keywords, Science Categories and Web of Science Categories
                if 'subjects' in work and work['subjects']:
                    all_subjects = [subj.get('subject', '') for subj in work['subjects'] if 'subject' in subj]
                    if all_subjects:
                        metadata['DE'] = '; '.join(all_subjects)  # DE only; WC/SC come from ML

                # Language
                if 'language' in work:
                    metadata['LA'] = work['language']
                
                # Publisher/Journal
                if 'publisher' in work:
                    metadata['SO'] = work['publisher']
                
                # Abstract
                if 'descriptions' in work:
                    abstracts = [desc.get('description', '') for desc in work['descriptions'] 
                               if desc.get('descriptionType', '').lower() == 'abstract']
                    if abstracts:
                        metadata['AB'] = abstracts[0]
                
                # Contributors (including affiliations)
                if 'contributors' in work:
                    affiliations = []
                    for contrib in work['contributors']:
                        if isinstance(contrib, dict) and 'affiliation' in contrib:
                            if isinstance(contrib['affiliation'], list):
                                affiliations.extend([aff.get('name', '') for aff in contrib['affiliation'] if 'name' in aff])
                            elif isinstance(contrib['affiliation'], str):
                                affiliations.append(contrib['affiliation'])
                    if affiliations:
                        metadata['C1'] = '; '.join(set(affiliations))  # Remove duplicates
                
                return metadata
    except Exception as e:
        print(f"DataCite API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_unpaywall(doi: str, email: str) -> dict:
    """Extract metadata from Unpaywall API (Free, requires email)"""
    try:
        url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
        response = _get_with_retry(url)
        
        if response.status_code == 200:
            work = response.json()
            metadata = {}
            
            # Authors
            if 'z_authors' in work:
                authors = [f"{a.get('given', '')} {a.get('family', '')}" for a in work['z_authors']]
                metadata['AU'] = '; '.join(authors)
            
            # Title
            if 'title' in work:
                metadata['TI'] = work['title']
            
            # Journal
            if 'journal_name' in work:
                metadata['SO'] = work['journal_name']
            
            # Publication Year
            if 'published_date' in work:
                try:
                    metadata['PY'] = work['published_date'][:4]
                except:
                    pass
            
            # Document Type
            if 'genre' in work:
                metadata['DT'] = work['genre']
            
            return metadata
    except Exception as e:
        print(f"Unpaywall API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_europepmc(doi: str) -> dict:
    """Extract metadata from Europe PMC API (Free)"""
    try:
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&format=json"
        response = _get_with_retry(url)
        
        if response.status_code == 200:
            data = response.json()
            if 'resultList' in data and 'result' in data['resultList'] and data['resultList']['result']:
                work = data['resultList']['result'][0]
                metadata = {}
                
                # DOI
                if 'doi' in work:
                    metadata['DI'] = work['doi']
                
                # Document Type
                if 'pubType' in work:
                    metadata['DT'] = work['pubType']
                
                # Title
                if 'title' in work:
                    metadata['TI'] = work['title']
                
                # Authors
                if 'authorString' in work:
                    metadata['AU'] = work['authorString']
                
                # Journal Title
                if 'journalTitle' in work:
                    metadata['SO'] = work['journalTitle']
                
                # Volume
                if 'journalVolume' in work:
                    metadata['VL'] = work['journalVolume']
                
                # Issue
                if 'journalIssue' in work:
                    metadata['IS'] = work['journalIssue']
                
                # Page Information
                if 'pageInfo' in work:
                    pages = work['pageInfo'].split('-')
                    if len(pages) == 2:
                        metadata['BP'] = pages[0].strip()  # Beginning Page
                        metadata['EP'] = pages[1].strip()  # Ending Page
                
                # Publication Year
                if 'pubYear' in work:
                    metadata['PY'] = work['pubYear']
                
                # ISSN
                if 'journalIssn' in work:
                    metadata['SN'] = work['journalIssn']
                
                # Abstract
                if 'abstractText' in work:
                    metadata['AB'] = work['abstractText']
                
                # URLs
                urls = []
                if 'sourceUrl' in work:
                    urls.append(work['sourceUrl'])
                if 'fullTextUrlList' in work and 'fullTextUrl' in work['fullTextUrlList']:
                    for url_info in work['fullTextUrlList']['fullTextUrl']:
                        if 'url' in url_info:
                            urls.append(url_info['url'])
                if urls:
                    metadata['UR'] = '; '.join(urls)
                
                # Citations
                if 'citedByCount' in work:
                    metadata['TC'] = work['citedByCount']
                
                # Open Access Information
                if 'isOpenAccess' in work:
                    oa_info = []
                    oa_info.append(f"is_oa: {work['isOpenAccess']}")
                    if 'fullTextUrlList' in work and 'fullTextUrl' in work['fullTextUrlList']:
                        for url_info in work['fullTextUrlList']['fullTextUrl']:
                            if 'availability' in url_info:
                                oa_info.append(f"availability: {url_info['availability']}")
                            if 'availabilityCode' in url_info:
                                oa_info.append(f"status: {url_info['availabilityCode']}")
                    if oa_info:
                        metadata['OA'] = '; '.join(oa_info)
                
                # Additional Information
                additional_info = []
                if 'inEPMC' in work:
                    additional_info.append(f"in_epmc: {work['inEPMC']}")
                if 'inPMC' in work:
                    additional_info.append(f"in_pmc: {work['inPMC']}")
                if 'hasPDF' in work:
                    additional_info.append(f"has_pdf: {work['hasPDF']}")
                if 'hasReferences' in work:
                    additional_info.append(f"has_references: {work['hasReferences']}")
                if 'hasTextMinedTerms' in work:
                    additional_info.append(f"has_text_mined_terms: {work['hasTextMinedTerms']}")
                if 'hasDbCrossReferences' in work:
                    additional_info.append(f"has_db_cross_references: {work['hasDbCrossReferences']}")
                if additional_info:
                    metadata['AI'] = '; '.join(additional_info)
                
                return metadata
    except Exception as e:
        print(f"Europe PMC API Error: {str(e)}")
    return {}

@lru_cache(maxsize=4096)
def extract_metadata_from_semantic_scholar(doi: str, api_key: str = None) -> dict:
    """Extract metadata from Semantic Scholar API (Free, API key optional but recommended)"""
    try:
        url = f"https://api.semanticscholar.org/v1/paper/{doi}"
        headers = {'x-api-key': api_key} if api_key else {}
        response = _get_with_retry(url, headers=headers)
        
        if response.status_code == 200:
            work = response.json()
            metadata = {}
            
            # Paper ID
            if 'paperId' in work:
                metadata['ID'] = work['paperId']
            
            # DOI
            if 'externalIds' in work and 'DOI' in work['externalIds']:
                metadata['DI'] = work['externalIds']['DOI']
            
            # Title
            if 'title' in work:
                metadata['TI'] = work['title']
            
            # Authors
            if 'authors' in work:
                authors = []
                for author in work['authors']:
                    if 'name' in author:
                        authors.append(author['name'])
                
                if authors:
                    metadata['AU'] = '; '.join(authors)
            
            # Abstract
            if 'abstract' in work:
                metadata['AB'] = work['abstract']
            
            # Publication Year
            if 'year' in work:
                metadata['PY'] = work['year']
            
            # Citations
            if 'citationCount' in work:
                metadata['TC'] = work['citationCount']
            
            # Reference Count
            if 'referenceCount' in work:
                metadata['RC'] = work['referenceCount']
            
            # URL
            if 'url' in work:
                metadata['UR'] = work['url']
            
            # Fields of Study -> DE only; WC/SC come from ML
            if 'fieldsOfStudy' in work and work['fieldsOfStudy']:
                metadata['DE'] = '; '.join(work['fieldsOfStudy'])
            
            # Additional Identifiers
            if 'externalIds' in work:
                external_ids = []
                for id_type, id_value in work['externalIds'].items():
                    if id_type != 'DOI':  # DOI already handled
                        external_ids.append(f"{id_type}: {id_value}")
                if external_ids:
                    metadata['EI'] = '; '.join(external_ids)
            
            return metadata
    except Exception as e:
        print(f"Semantic Scholar API Error: {str(e)}")
    return {}

def truncate_url_list(url_list: str, max_length: int = 2079) -> str:
    """Truncate the URL list to fit Excel's URL length limit"""
    # If the URL list is empty or already under the limit, return it as is
    if not url_list or len(url_list) <= max_length:
        return url_list

    # Split the URLs
    urls = url_list.split('; ')
    result = []
    current_length = 0

    # For each URL
    for i, url in enumerate(urls):
        # The first URL needs no separator; the others need '; ' (2 characters)
        separator_length = 0 if not result else 2

        # Total length if we add this URL
        new_length = current_length + len(url) + separator_length

        # If adding this URL will not exceed the limit
        if new_length <= max_length - 3:  # reserve 3 characters for "..."
            result.append(url)
            current_length = new_length
        else:
            # Limit reached: append "..." and break out of the loop
            result.append("...")
            break

    # Join the URLs
    return '; '.join(result)

def extract_metadata(doi: str, current_data: dict, scopus_api_key: str = None, semantic_scholar_key: str = None, unpaywall_email: str = None, crossref_email: str = None, openalex_api_key: str = None) -> dict:
    """Try to extract metadata from multiple sources"""
    metadata = current_data.copy()
    api_sources = {}  # Track which fields came from which API

    # For the early exit: fields the APIs can fill. OI (ORCID)/RI/ROR/CC were newly
    # added (author/institution identifiers — for disambiguation).
    # CR is NOT in the list: no API returns the citation list in WoS grammar
    # (OpenAlex gives URLs, Crossref gives raw JSON) — CR comes only from the source file.
    _FILLABLE = ("DI", "DT", "AU", "AF", "TI", "PY", "SO", "PU", "SN", "UR", "AB", "DE",
                 "C1", "TC", "LA", "WC", "SC", "OI", "RI", "ROR", "CC")

    def _is_blank(v) -> bool:
        if v is None:
            return True
        try:
            if pd.isna(v):
                return True
        except (TypeError, ValueError):
            pass
        return str(v).strip() in ("", "nan", "NaN", "None")

    def _remaining() -> bool:
        return any(_is_blank(metadata.get(f)) for f in _FILLABLE)

    try:
        # Get API credentials from config file
        if not scopus_api_key:
            scopus_api_key = get_api_credential('scopus', 'api_key')
        if not semantic_scholar_key:
            semantic_scholar_key = get_api_credential('semantic_scholar', 'api_key')
        if not unpaywall_email:
            unpaywall_email = get_api_credential('unpaywall', 'email')
        if not crossref_email:
            crossref_email = get_api_credential('crossref', 'email')

        if not scopus_api_key:
            print("\nWarning: Scopus API key not found in API_config.json")
            print("You can add it to enable Scopus metadata enrichment.")
            print("Continuing with other data sources...")

        if not unpaywall_email:
            print("\nWarning: Unpaywall email not found in API_config.json")
            print("You can add it to enable Unpaywall metadata enrichment.")
            print("Continuing with other data sources...")
        
        # CrossRef
        print(f"\nTrying CrossRef API...", end='')
        try:
            crossref_data = extract_metadata_from_crossref(doi, crossref_email)
            if crossref_data:
                for key, value in crossref_data.items():
                    if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                        # Truncate fields that contain URLs
                        if isinstance(value, str) and ('http://' in value or 'https://' in value):
                            value = truncate_url_list(value)
                        metadata[key] = value
                        if pd.notna(value) and str(value).strip() != '':
                            api_sources[key] = 'CrossRef'
                print(" [SUCCESS]")
            else:
                print(" [NO DATA]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
        # OpenAlex
        print(f"Trying OpenAlex API...", end='')
        try:
            openalex_data = extract_metadata_from_openalex(doi, crossref_email, openalex_api_key)
            if openalex_data:
                for key, value in openalex_data.items():
                    if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                        # Truncate fields that contain URLs
                        if isinstance(value, str) and ('http://' in value or 'https://' in value):
                            value = truncate_url_list(value)
                        metadata[key] = value
                        if pd.notna(value) and str(value).strip() != '':
                            api_sources[key] = 'OpenAlex'
                print(" [SUCCESS]")
            else:
                print(" [NO DATA]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")

        # Early exit: if no fields are left to fill after CrossRef + OpenAlex, do not
        # try the other 5 APIs at all (the biggest speed gain).
        if not _remaining():
            for field in metadata:
                if isinstance(metadata[field], str) and ('http://' in metadata[field] or 'https://' in metadata[field]):
                    metadata[field] = truncate_url_list(metadata[field])
            if api_sources:
                metadata['API_Sources'] = api_sources
            return metadata

        # Scopus
        if scopus_api_key:
            print(f"Trying Scopus API...", end='')
            try:
                scopus_data = extract_metadata_from_scopus(doi, scopus_api_key)
                if scopus_data:
                    for key, value in scopus_data.items():
                        if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                            metadata[key] = value
                            if pd.notna(value) and str(value).strip() != '':
                                api_sources[key] = 'Scopus'
                    print(" [SUCCESS]")
                else:
                    response = requests.get(f"https://api.elsevier.com/content/abstract/doi/{doi}",
                                         headers={'X-ELS-APIKey': scopus_api_key, 'Accept': 'application/json'}, timeout=REQUEST_TIMEOUT)
                    print(f" [NO DATA - Status: {response.status_code}, Response: {response.text[:100]}...]")
            except Exception as e:
                print(f" [ERROR: {str(e)}]")
        
        # DataCite
        print(f"Trying DataCite API...", end='')
        try:
            response = requests.get(f"https://api.datacite.org/dois/{doi}", headers={'Accept': 'application/vnd.api+json'}, timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                print(f" [NO DATA - Status: {response.status_code}]")
            else:
                datacite_data = extract_metadata_from_datacite(doi)
                if datacite_data:
                    for key, value in datacite_data.items():
                        if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                            metadata[key] = value
                            if pd.notna(value) and str(value).strip() != '':
                                api_sources[key] = 'DataCite'
                    print(" [SUCCESS]")
                else:
                    print(f" [NO DATA - Empty Response]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
        # Unpaywall
        if unpaywall_email:
            print(f"Trying Unpaywall API...", end='')
            try:
                unpaywall_data = extract_metadata_from_unpaywall(doi, unpaywall_email)
                if unpaywall_data:
                    for key, value in unpaywall_data.items():
                        if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                            metadata[key] = value
                            if pd.notna(value) and str(value).strip() != '':
                                api_sources[key] = 'Unpaywall'
                    print(" [SUCCESS]")
                else:
                    print(" [NO DATA]")
            except Exception as e:
                print(f" [ERROR: {str(e)}]")
        
        # Europe PMC
        print(f"Trying Europe PMC API...", end='')
        try:
            response = requests.get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&format=json", timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                print(f" [NO DATA - Status: {response.status_code}]")
            else:
                europepmc_data = extract_metadata_from_europepmc(doi)
                if europepmc_data:
                    for key, value in europepmc_data.items():
                        if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                            metadata[key] = value
                            if pd.notna(value) and str(value).strip() != '':
                                api_sources[key] = 'Europe PMC'
                    print(" [SUCCESS]")
                else:
                    data = response.json()
                    hit_count = data.get('hitCount', 0)
                    print(f" [NO DATA - Hit Count: {hit_count}]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
        # Semantic Scholar
        print(f"Trying Semantic Scholar API...", end='')
        try:
            semantic_data = extract_metadata_from_semantic_scholar(doi, semantic_scholar_key)
            if semantic_data:
                for key, value in semantic_data.items():
                    if pd.isna(metadata.get(key, None)) or str(metadata.get(key, '')).strip() == '':
                        metadata[key] = value
                        if pd.notna(value) and str(value).strip() != '':
                            api_sources[key] = 'Semantic Scholar'
                print(" [SUCCESS]")
            else:
                print(" [NO DATA]")
        except Exception as e:
            print(f" [ERROR: {str(e)}]")
        
        # Add the API sources
        if api_sources:
            metadata['API_Sources'] = api_sources
        
        # Final check: check all fields that contain URLs and truncate them if necessary
        for field in metadata:
            if isinstance(metadata[field], str) and ('http://' in metadata[field] or 'https://' in metadata[field]):
                metadata[field] = truncate_url_list(metadata[field])
        
        return metadata
        
    except Exception as e:
        print(f"Error processing DOI {doi}: {str(e)}")
        return current_data 