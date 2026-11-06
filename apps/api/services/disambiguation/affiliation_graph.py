"""Affiliation standardization candidates: a field-aware multi-key graph.

The builder turns the C1 addresses of a dataset into candidates for the affiliation
standardization step: groups of address spellings that name the same institution.

Why: the previous builder blocked addresses on their first word and linked them with
single-linkage Jaro-Winkler, which chained thousands of different institutions into one
block (one LLM request held 2,051 addresses on the sample dataset). This builder links on
parsed fields and cannot chain.

Output schema of blocking.build_affiliation_blocks:
    block = {"key": str (unique), "auto": [tier-1 candidates], "members": [tier-2 candidates]}
    candidate = {"id", "variants" (raw address strings, most complete first),
                 "records" (row UIDs, <= 100), "tier"}

Pure stdlib + existing repo helpers, deterministic, cancellable, O(n log n)-ish.

  0. mentions -> unique raw address strings ("variants"), records per variant.
  1. FIELD PARSER. Comma parts (a parenthesised span is a part of its own, a part made only of
     structure words such as "UNIVERSITY" is glued to the previous one) are typed:
       COUNTRY  last part (c1_utils.canonical_country; WoS "MN 55905 USA" handled)
       GEO      street / postal / state part (starts with a digit, postal code, US state code in
                a short part, street word, "University Park"-style city)  -> dropped
       rank 1   UNIT: department / faculty / division / chair / group ...
       rank 2   LAB:  laboratory / centre / school / clinic (unit or organisation)
       rank 3   ORG:  institute / college / hospital / academy / ministry / company ...
       rank 4   UNIV: university / polytechnic; "X University Faculty of Y" is cut to "X University"
       NAME     unmarked part that is the organisation name ("MIT", "ETH ZURICH", "VIRGINIA TECH"):
                no rank-4 part in the address and no rank >= 2 part before it
       PLACE    the last unmarked part after the organisation (<= 3 tokens, not organisation-like),
                plus the short part before it when the last part is a 1-token region code
     Institution field = parts of the top rank (+ NAME parts). Unit field = the other kept parts.
     Tokens are canonicalised with a general WoS abbreviation dictionary; a strict 7-char stem
     for the tier-1 signature, a loose 5-char stem for linking.
  2. TIER-1 (safe, pre-checked in the UI): same country + identical STRICT signature of the
     whole non-geographic address (institution + unit tokens) + identical institution numbers
     (Paris 05 != Paris 07) + no city conflict, i.e. a spelling / abbreviation / word-order /
     punctuation / postal-code-only difference. An address that lost a part to normalisation
     (non-Latin institution name) is never tier 1. A spelling group is a
     tier-1 proposal only when no other variant of the dataset links to it at theta_cluster;
     otherwise the whole group goes to the LLM request as ONE candidate (the LLM sees the
     family together), so nothing is ever hidden from the LLM.
  3. MULTI-KEY BLOCKING: inverted index on (country, token) for institution AND place tokens
     with country-local document frequency <= max_df, plus acronym keys: a short single
     token on one side ("UCL", "MIT", "SUNY") against the initials of the organisation words
     (and their prefixes) on the other side ("university college london" -> ucl,
     "state university new york oneonta" -> suny, sunyo).
  4. SCORED EDGES (field-aware): IDF-weighted Jaccard on the INSTITUTION field, IDF computed
     per country (so "hong kong" / "california" / "indian" carry little weight inside their
     own country), structure words at a small constant weight, a campus token that appears
     as the CITY of the other address counts as shared ("UNIV CALIF, LOS ANGELES" vs
     "UNIVERSITY OF CALIFORNIA LOS ANGELES"), organisation-type conflict (univ vs lab) is a
     penalty, an acronym hit floors the score. Different known countries = cannot-link.
  5. CLUSTERING THAT CANNOT CHAIN: edges in descending score order, two clusters merge only
     when every cross pair scores >= theta_request (complete-linkage floor), the mean cross
     score >= theta_cluster and the result has <= cap variants. -> tier-2 candidates.
  6. REQUESTS: connected components of the candidate graph at theta_request, cut into
     <= cap variants along the sorted institution signature with candidates kept whole,
     then first-fit packed per country in signature order (neighbouring institutions share
     one request; every request <= cap variants).
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Callable, Optional

import pandas as pd

from bibex_core.modules.c1_utils import _US_STATES, canonical_country
from services.pdcompat import is_missing

from .blocking import _split_affs, _uid_of_label, _uids
from .similarity import normalize_name

CancelCheck = Optional[Callable[[], bool]]

# ------------------------------------------------------------------ knobs
CAP = 40                 # max variants per LLM request (hard requirement)
THETA_CLUSTER = 0.65     # mean cross-score needed to merge two candidates (tier-2 candidate)
THETA_REQUEST = 0.40     # edge threshold: same LLM request; also the complete-linkage floor
MAX_DF = 300             # (country, token) keys with a higher posting list do not generate pairs
W_STRUCT = 0.25          # weight of structure words (univ, inst, ...) in the weighted Jaccard
TYPE_PENALTY = 0.6       # organisation-type conflict (univ vs lab) multiplies the score
ACRONYM_FLOOR = 0.70     # score floor for an acronym hit
NAME_FALLBACK = 0.9      # factor when one side has no organisation field (unit tokens used)
IDF_MIN_N = 30           # smoothing: a country with fewer variants is weighted as if it had this many
_CANCEL_EVERY = 256
_PAIR_CHECK_EVERY = 4096

# ------------------------------------------------------------------ token canonicalisation
# General WoS address abbreviations (not keyed to any institution). Every form maps to the
# first form of its group. "tu" expands to two tokens.
_ABBR_GROUPS = """
univ university universidad universita universite universitat universiteit universidade universitas universitaet
dept department departamento dipartimento departement
inst institute institut instituto istituto institution institutes
coll college colegio collegio
sch school schools
ctr center centre centro centers centres
hosp hospital hospitals
clin clinic clinical
lab labs laboratory laboratories laboratoire laboratorio
technol technology technological tech technical technische technica tecnologico tecnologica
sci science sciences scientific
engn engineering eng engineer
natl national nacional nazionale
med medical medicine
res research
fac faculty facultad faculte facolta
educ education educational
econ economics economic economy
psychol psychology psychological
comp computer computing computational
informat informatics information
mgmt management
int intl international
hlth health
polytech polytechnic polytechnical politecnico
acad academy academia academic
fdn foundation
elect electrical electronic electronics
mech mechanical mechanics
chem chemistry chemical
phys physics physical
biol biology biological
math mathematics mathematical
stat statistics statistical
environm environmental environment
agr agriculture agricultural
commun communication communications
publ public
appl applied
grad graduate
calif california
penn pennsylvania
nurs nursing
vet veterinary
dev development
syst system systems sys
bus business
soc social
human humanities
lang language languages
ling linguistics
hist history
polit political politics
philos philosophy
sociol sociology
architect architecture architectural
div division
genet genetics
mol molecular
pharm pharmacy pharmaceutical
dent dentistry dental
mfg manufacturing
analyt analytics analytical
mkt marketing
adv advanced
surg surgery surgical
geog geography geographic
ind industrial industry
biomed biomedical
mat materials material
fed federal
s south
n north
e east
w west
st saint
pk park
ave avenue
rd road
bldg building
assoc association
coop cooperative
corp corporation
cty county
prov province provincial
reg regional region
cent central
gen general
nat natural
sect section
behav behavioral behaviour behavior behavioural
biochem biochemistry
pediat pediatrics pediatric paediatrics
neurol neurology neurological
cardiol cardiology
oncol oncology
radiol radiology
otolaryngol otolaryngology otorhinolaryngology
anesthesiol anesthesiology
ophthalmol ophthalmology
immunol immunology
microbiol microbiology
epidemiol epidemiology
neurosci neuroscience neurosciences
transport transportation
construct construction
instruct instruction instructional
learn learning
innovat innovation
ecol ecology ecological
geol geology geological
meteorol meteorology
astron astronomy astronomical
anthropol anthropology
archaeol archaeology
theol theology
lit literature
minist ministry ministerio ministere
adm administration administrative
serv service services
grp group
lib library libraries biblioteca bibliotheque
amer american americana americano
brit british
govt government
mil military
def defense defence
intens intensive
infirm infirmary
vis vision visual
mt mount
petr petroleum
isl island islands
academies academies
soc social society sociedad
educacion educacion
internacional internacional internationale internazionale
nacl nacl
tecnol tecnol
"""
_ABBR: dict[str, str] = {}
for _line in _ABBR_GROUPS.strip().splitlines():
    _forms = _line.split()
    for _f in _forms:
        _ABBR.setdefault(_f, _forms[0])
for _f in ("academies",):
    _ABBR[_f] = "acad"
for _f in ("educacion",):
    _ABBR[_f] = "educ"
for _f in ("internacional", "internationale", "internazionale"):
    _ABBR[_f] = "int"
for _f in ("nacl",):
    _ABBR[_f] = "natl"
for _f in ("tecnol",):
    _ABBR[_f] = "technol"
_EXPAND: dict[str, tuple[str, ...]] = {"tu": ("technol", "univ")}
# descriptors that name a unit inside "X University School of Y"-style parts
_UNIT_DESC = {"bus", "med", "law", "engn", "educ", "nurs", "mgmt", "sci", "arts", "art", "design", "hlth",
              "econ", "pharm", "dent", "vet", "agr", "soc", "informat", "comp", "grad", "polit", "human",
              "architect", "commun", "psychol", "publ", "phys", "chem", "biol", "math", "lit", "lang"}
# geographic descriptors that may match a unit/place token of the other address (campus = city)
_GEO_DESC = {"cty", "city", "pk", "campus", "bay", "hill", "park", "station", "heights", "county", "cent",
             "north", "south", "east", "west", "n", "s", "e", "w", "new", "st"}
_CUT_TOKENS = {"fac", "dept", "sch", "div", "lab"}
_SPECIAL = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ß": "ss",
                          "đ": "d", "Đ": "d", "ł": "l", "Ł": "l", "œ": "oe",
                          "þ": "th", "ı": "i"})

_STOP = {"the", "of", "and", "for", "de", "la", "del", "di", "du", "des", "et", "und", "fur", "le", "les",
         "at", "in", "on", "a", "an", "y", "e", "da", "do", "amp", "u", "r", "peoples", "sar", "to", "der",
         "das", "dem", "den", "von", "van", "het", "el", "al", "no", "c", "o", "room", "office"}
# structure words: say nothing about WHICH institution
_STRUCT = {"univ", "inst", "coll", "sch", "ctr", "dept", "fac", "lab", "hosp", "div", "campus", "unit",
           "program", "programme", "section", "group", "grp", "chair", "area", "ltd", "inc", "co",
           "gmbh", "corp", "clin", "acad", "fdn", "polytech", "team", "project", "sect", "minist",
           "council", "agency", "bureau", "assoc", "trust", "commission", "authority"}
_ORG_TYPE_TOKENS = ("univ", "polytech", "coll", "inst", "sch", "acad", "hosp", "clin", "ctr", "lab",
                    "fdn", "corp", "minist")
_UNIT_TOKENS = {"dept", "fac", "div", "chair", "section", "sect", "unit", "program", "programme",
                "group", "grp", "team"}
_LAB_TOKENS = {"lab", "ctr", "sch", "clin"}
_ORG_TOKENS = {"inst", "coll", "hosp", "acad", "fdn", "corp", "ltd", "inc", "gmbh", "minist", "council",
               "agency", "bureau", "assoc", "trust", "commission", "authority"}
# tokens that mark an unmarked part as an organisation name rather than a city
_ORGISH = {"technol", "res", "sci", "natl", "fed", "state", "int", "engn", "med", "educ", "syst",
           "mgmt", "bus", "informat", "comp", "hlth", "studies", "studi", "design", "project",
           "team", "office", "serv", "adm"}
_RANK_UNIT, _RANK_LAB, _RANK_ORG, _RANK_UNIV = 1, 2, 3, 4
_UNIV_CITY_RE = re.compile(r"^(univ|university|college|coll)\s+(park|pk|station|place|city|heights)$")
_STREET_WORDS = {"st", "rd", "ave", "avenue", "road", "street", "blvd", "boulevard", "box", "po", "gpo",
                 "pob", "mailstop", "bag", "strasse", "str", "platz", "pl", "via", "rue", "calle", "floor",
                 "way", "square", "sq", "drive", "dr", "lane", "hall", "bldg", "building", "block"}
_ORG_MARKERS_RAW = ("univ", "inst", "coll", "sch", "hosp", "ctr", "acad", "polytech")
_PAREN_RE = re.compile(r"\(([^()]*)\)")


@lru_cache(maxsize=200000)
def _norm(s: str) -> str:
    """normalize_name with apostrophes removed and non-decomposable letters mapped first
    (KING'S -> KINGS, not KING S; OSTFOLD with a slashed O -> OSTFOLD, not STFOLD). Memoised
    (bounded): comma parts repeat across addresses (cities, countries, department names)."""
    return normalize_name(str(s).replace("'", "").replace("’", "").replace("‘", "").translate(_SPECIAL))



def _canon_strict(t: str) -> str:
    c = _ABBR.get(t)
    if c:
        return c
    if t.startswith("univ"):
        return "univ"
    return t[:7]


def _canon_loose(t: str) -> str:
    c = _ABBR.get(t)
    if c:
        return c
    if t.startswith("univ"):
        return "univ"
    return t[:5]


def _tokens(part: str, canon) -> list[str]:
    out: list[str] = []
    for t in _norm(part).split():
        if t in _STOP or t.isdigit() or (len(t) == 1 and t not in ("s", "n", "e", "w")):
            continue
        if t in _EXPAND:
            out.extend(_EXPAND[t])
            continue
        out.append(canon(t))
    return out


def _country_of(parts: list[str]):
    """(canonical country or None, parts without the country words)."""
    if not parts:
        return None, parts
    last = parts[-1].rstrip(". ")
    c = canonical_country(last)
    if c:
        return c, parts[:-1]
    toks = last.split()
    for k in (1, 2, 3):
        if len(toks) > k:
            c = canonical_country(" ".join(toks[-k:]))
            if c:
                # WoS "MN 55905 USA": the remaining head is geographic (state + postal)
                return c, parts[:-1] + [" ".join(toks[:-k])]
    return None, parts


def _is_geo(part: str, country) -> bool:
    p = part.strip()
    if not p:
        return True
    low = _norm(p)
    if not low or _UNIV_CITY_RE.match(low):
        return True
    toks = low.split()
    if low[0].isdigit():
        return True                      # street number / postal code first
    if any(ch.isdigit() for ch in low):
        # a numbered part is a street / postal / box unless it carries an organisation word
        return not any(_ABBR.get(t, t) in _ORG_MARKERS_RAW or t.startswith("univ") for t in toks)
    # a leading "st" is "Saint" in a city name ("ST PAUL", "ST LOUIS", "ST ANDREWS"): it only makes a
    # street when the part ends with a street word ("ST GEORGES RD") or is just "ST"
    if (toks[0] in _STREET_WORDS and (toks[0] != "st" or len(toks) == 1)) or (
            len(toks) >= 2 and toks[-1] in _STREET_WORDS):
        return True
    if len(toks) <= 3 and country in (None, "United States") \
            and any(t.upper() in _US_STATES for t in toks if len(t) == 2):
        return True
    return False


def _rank(toks_loose: list[str]) -> int:
    s = set(toks_loose)
    if "univ" in s or "polytech" in s:
        return _RANK_UNIV
    if s & _UNIT_TOKENS:
        return _RANK_UNIT
    if s & _ORG_TOKENS:
        return _RANK_ORG
    if s & _LAB_TOKENS:
        return _RANK_LAB
    return 0


def _split_parts(raw: str) -> list[str]:
    """Comma parts; a parenthesised span becomes a part of its own ("SWANSEA UNIVERSITY (BAY
    CAMPUS)" -> "SWANSEA UNIVERSITY", "BAY CAMPUS"); a part made only of structure words is a
    comma-split fragment and is glued to the previous part ("ZHEJIANG, UNIVERSITY")."""
    out: list[str] = []
    for chunk in raw.split(","):
        pos = 0
        for m in _PAREN_RE.finditer(chunk):
            head = chunk[pos:m.start()].strip()
            if head:
                out.append(head)
            inner = m.group(1).strip()
            if inner:
                out.append(inner)
            pos = m.end()
        tail = chunk[pos:].strip()
        if tail:
            out.append(tail)
    merged: list[str] = []
    for p in out:
        toks = _norm(p).split()
        if merged and toks and all(t in _STRUCT or t in _STOP or _ABBR.get(t, t) in _STRUCT for t in toks):
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


def _cut_univ_part(words: list[str]) -> list[str]:
    """'marmara university faculty of medicine' -> 'marmara university';
    'university of bristol business school' -> 'university of bristol'."""
    canon = [_ABBR.get(w, w) for w in words]
    canon = ["univ" if c.startswith("univ") else c for c in canon]
    try:
        ui = canon.index("univ")
    except ValueError:
        return words
    for k in range(ui + 1, len(canon)):
        if canon[k] in _CUT_TOKENS and k >= 2:
            head = words[:k]
            while len(head) > 2 and _ABBR.get(head[-1], head[-1][:5]) in _UNIT_DESC:
                head.pop()
            return head
    return words


class _Addr:
    __slots__ = ("raw", "norm", "country", "inst", "full_strict", "full_loose", "place",
                 "org_type", "acr_tokens", "acr_initials", "records", "unit", "nums", "lost")

    def __init__(self, raw: str):
        self.raw = raw
        self.norm = normalize_name(raw)
        self.records: set[int] = set()
        parts = [p.strip() for p in _split_parts(raw) if p.strip()]
        self.country, parts = _country_of(parts)
        typed = []  # (rank, loose tokens, strict tokens, words)
        nums: set[str] = set()
        self.lost = False   # a part with letters vanished in normalisation (non-Latin script)
        for p in parts:
            if not _norm(p).strip() and any(ch.isalpha() for ch in p):
                self.lost = True
            if _is_geo(p, self.country):
                continue
            words = [w for w in _norm(p).split() if w not in _STOP and not w.isdigit()]
            if not words:
                continue
            # institution numbers of a non-geographic part ("PARIS 05", "LYON 1"), normalised
            # ("05" == "5"); postal codes and street numbers live in geographic parts (dropped
            # above) or have more than 3 digits
            nums.update(str(int(w)) for w in _norm(p).split() if w.isdigit() and len(w) <= 3)
            loose = _tokens(" ".join(words), _canon_loose)
            if not loose:
                continue
            rk = _rank(loose)
            if rk == _RANK_UNIV:
                words = _cut_univ_part(words)
                loose = _tokens(" ".join(words), _canon_loose)
            typed.append((rk, loose, _tokens(" ".join(words), _canon_strict), words))
        marked = [i for i, t in enumerate(typed) if t[0] > 0]
        last_marked = marked[-1] if marked else -1
        top = max((t[0] for t in typed), default=0)
        # the city: the last unmarked part after the organisation (short, not organisation-like),
        # plus a short unmarked part right before it (state / region)
        place_idx: set[int] = set()
        li = len(typed) - 1
        if li >= 0 and typed[li][0] == 0 and li > last_marked and len(typed[li][1]) <= 3 \
                and not (set(typed[li][1]) & _ORGISH) and (marked or li > 0):
            place_idx.add(li)
            # "LUCKNOW, UP": a 1-token region code last -> the short unmarked part before it is the city
            if len(typed[li][3]) == 1 and len(typed[li][3][0]) <= 3 and li - 1 > last_marked and li - 1 > 0 \
                    and typed[li - 1][0] == 0 and len(typed[li - 1][1]) <= 2 \
                    and not (set(typed[li - 1][1]) & _ORGISH):
                place_idx.add(li - 1)
        inst: list[str] = []
        unit: set[str] = set()
        place: set[str] = set()
        full_strict: set[str] = set()
        full_loose: set[str] = set()
        acr_init: set[str] = set()
        self.org_type = None
        seen_org = False      # a rank >= 2 part has been seen (organisation already named)
        for i, (rk, loose, strict, words) in enumerate(typed):
            if i in place_idx:
                place.update(loose)
                continue
            full_loose.update(loose)
            full_strict.update(strict)
            is_name = rk == 0 and top < _RANK_UNIV and not seen_org
            if rk >= _RANK_LAB:
                seen_org = True
            if not ((rk == top and rk > 0) or is_name):
                unit.update(loose)
                continue
            inst.extend(loose)
            if self.org_type is None:
                for ot in _ORG_TYPE_TOKENS:
                    if ot in loose:
                        self.org_type = ot
                        break
            # initials of the organisation words (structure words included) and their prefixes
            if 2 <= len(words) <= 7:
                initials = "".join(w[0] for w in words)
                for k in range(2, len(initials) + 1):
                    acr_init.add(initials[:k])
        self.inst = frozenset(inst)
        self.unit = frozenset(unit)
        self.full_strict = frozenset(full_strict)
        self.full_loose = frozenset(full_loose)
        self.place = frozenset(place)
        self.nums = frozenset(nums)
        # short organisation tokens that may be acronyms (UCL, MIT, SUNY, ETH, NYU, RWTH)
        self.acr_tokens = frozenset(t for t in inst if t not in _STRUCT and 2 <= len(t) <= 5
                                    and t not in _ABBR_VALUES and t.isalpha())
        self.acr_initials = frozenset(acr_init)

    def cross(self) -> frozenset:
        """Tokens of this address that an INSTITUTION token of the other side may match:
        the city, and unit/qualifier tokens that are proper nouns or geographic descriptors
        ("BALTIMORE COUNTY", "CNRS-CEA", "BAY CAMPUS")."""
        return self.place | frozenset(t for t in self.unit if t not in _ABBR_VALUES or t in _GEO_DESC)


_ABBR_VALUES = frozenset(_ABBR.values())


# ------------------------------------------------------------------ similarity

class _Node:
    """Per-node precomputation for the pair loop: weighted token dicts and their sums."""
    __slots__ = ("iw", "isum", "fw", "fsum", "cross", "place", "type", "acr_t", "acr_i", "country")

    def __init__(self, a: _Addr, wc: dict):
        # sorted token order: identical float sums in every process (hash seeds differ)
        self.iw = {t: wc.get(t, W_STRUCT) for t in sorted(a.inst)}
        self.isum = sum(self.iw.values())
        self.fw = {t: wc.get(t, W_STRUCT) for t in sorted(a.full_loose)}
        self.fsum = sum(self.fw.values())
        self.cross = a.cross()
        self.place = a.place
        self.type = a.org_type
        self.acr_t = a.acr_tokens
        self.acr_i = a.acr_initials
        self.country = a.country


def _wj_fast(aw: dict, asum: float, bw: dict, bsum: float, extra_a: frozenset, extra_b: frozenset) -> float:
    inter = common = 0.0
    for t, wt in aw.items():
        if t in bw:
            inter += wt
            common += wt
        elif t in extra_b:
            inter += wt
    for t, wt in bw.items():
        if t not in aw and t in extra_a:
            inter += wt
    union = asum + bsum - common
    return round(inter / union, 9) if union else 0.0


def _score_nodes(x: _Node, y: _Node) -> float:
    if x.country and y.country and x.country != y.country:
        return 0.0
    if x.iw and y.iw:
        s = _wj_fast(x.iw, x.isum, y.iw, y.isum, x.cross, y.cross)
        if x.type and y.type and x.type != y.type:
            s *= TYPE_PENALTY
    else:
        s = NAME_FALLBACK * _wj_fast(x.fw, x.fsum, y.fw, y.fsum, x.place, y.place)
    if s < ACRONYM_FLOOR and (not x.acr_t.isdisjoint(y.acr_i) or not y.acr_t.isdisjoint(x.acr_i)):
        s = ACRONYM_FLOOR
    return round(s, 9)


# ------------------------------------------------------------------ builder

def _c1_cell(row) -> str:
    """Address cell of a row: C1, or C1raw when C1 is missing or blank. A missing cell
    (None, NaN, pd.NA) is never turned into the text "nan"."""
    for col in ("C1", "C1raw"):
        v = row.get(col)
        if not is_missing(v) and str(v).strip():
            return str(v)
    return ""


def _slug(country) -> str:
    return re.sub(r"[^a-z0-9]+", "", (country or "xx").lower())[:8] or "xx"


def _cand(key: str, ci, variants: list[_Addr], uid_of, tier: int, extra_records=()) -> dict:
    recs: set[int] = set(extra_records)
    for v in variants:
        recs |= v.records
    # most complete spelling first: the pipeline takes variants[0] as the canonical form
    raws = sorted((v.raw for v in variants), key=lambda r: (-len(r), r))
    return {"id": f"{key}_{ci}", "variants": raws, "records": _uids(recs, uid_of, 100), "tier": tier}


def build_affiliation_candidates(df: pd.DataFrame, should_cancel: CancelCheck = None, *,
                                 theta_cluster: float = THETA_CLUSTER, theta_request: float = THETA_REQUEST,
                                 max_df: int = MAX_DF, cap: int = CAP) -> list[dict]:
    """Affiliation blocks of a dataset (schema in the module docstring). Returns [] when
    should_cancel() turns True at one of its check points."""
    if "C1" not in df.columns and "C1raw" not in df.columns:
        return []
    uid_of = _uid_of_label(df)

    # ---- STEP 0: mentions -> unique variants
    addrs: dict[str, _Addr] = {}
    for ri, (idx, row) in enumerate(df.iterrows()):
        if should_cancel and ri % _CANCEL_EVERY == 0 and should_cancel():
            return []
        for aff in _split_affs(_c1_cell(row)):
            a = addrs.get(aff)
            if a is None:
                if not normalize_name(aff):
                    continue
                a = addrs[aff] = _Addr(aff)
            a.records.add(int(idx))
    variants = sorted(addrs.values(), key=lambda a: (a.norm, a.raw))
    n = len(variants)
    if n < 2:
        return []

    # ---- IDF per country over distinct variants (institution + unit + place tokens)
    n_country: Counter = Counter()
    dfc: Counter = Counter()
    for a in variants:
        n_country[a.country] += 1
        for t in a.full_loose | a.place:
            dfc[(a.country, t)] += 1
    w: dict = {}      # country -> {token: weight}
    for (c, t), k in dfc.items():
        if t in _STRUCT:
            continue
        w.setdefault(c, {})[t] = math.log(1.0 + max(n_country[c], IDF_MIN_N) / k)

    # ---- STEP 2: tier-1 spelling groups = identical (country, strict signature), no city conflict
    sig_groups: dict[tuple, list[int]] = defaultdict(list)
    for i, a in enumerate(variants):
        if a.full_strict and not a.lost:
            # numbers of non-geographic parts are part of the signature (Paris 05 != Paris 07);
            # an address that lost a part to normalisation is never tier 1 (it may still be linked)
            sig_groups[(a.country, a.full_strict, a.nums)].append(i)
    group_of = list(range(n))             # variant -> node id (smallest member index)
    node_members: dict[int, list[int]] = {}
    for gi, key in enumerate(sorted(sig_groups, key=lambda k: (str(k[0]), sorted(k[1]), sorted(k[2])))):
        if should_cancel and gi % _CANCEL_EVERY == 0 and should_cancel():
            return []
        ids = sig_groups[key]
        if len({variants[i].norm for i in ids}) < 2:
            continue
        by_city: dict[frozenset, list[int]] = defaultdict(list)
        for i in ids:
            by_city[variants[i].place].append(i)
        merged: list[tuple[set, list[int]]] = []   # PERTH / PERTH WA are one place (subset);
        for c in sorted((c for c in by_city if c), key=lambda c: (len(c), sorted(c))):  # SAN DIEGO / SAN FRANCISCO are not
            for m in merged:
                if m[0] <= c or c <= m[0]:
                    m[0].update(c)
                    m[1].extend(by_city[c])
                    break
            else:
                merged.append((set(c), list(by_city[c])))
        nocity = by_city.get(frozenset(), [])
        if len(merged) == 1:
            merged[0][1].extend(nocity)    # variants without a city join the only city
        elif not merged and nocity:
            merged.append((set(), nocity))
        for _c, members in merged:
            if len({variants[i].norm for i in members}) < 2:
                continue
            rep = min(members)
            for i in members:
                group_of[i] = rep
            node_members[rep] = sorted(members)

    nodes = sorted({group_of[i] for i in range(n)})
    node_var = [variants[nid] for nid in nodes]
    node_size = [len(node_members.get(nid, [nid])) for nid in nodes]
    node_obj = [_Node(a, w.get(a.country, {})) for a in node_var]

    # ---- STEP 3: multi-key blocking (country, token) over institution + place tokens, acronym keys
    index: dict[tuple, list[int]] = defaultdict(list)
    for k, a in enumerate(node_var):
        toks = (a.inst if a.inst else a.full_loose) | a.place
        for t in toks:
            if t not in _STRUCT and dfc[(a.country, t)] <= max_df:
                index[(a.country, t)].append(k)
        for acr in a.acr_tokens | a.acr_initials:
            index[(a.country, "@" + acr)].append(k)

    # ---- STEP 4: scored edges (>= theta_request)
    edges: dict[tuple[int, int], float] = {}
    seen_pairs: set[tuple[int, int]] = set()
    n_cmp = 0
    for key in sorted(index, key=lambda kk: (str(kk[0]), kk[1])):
        ids = index[key]
        if len(ids) < 2 or len(ids) > max_df:
            continue
        for x in range(len(ids)):
            i = ids[x]
            ai = node_obj[i]
            for y in range(x + 1, len(ids)):
                j = ids[y]
                if (i, j) in seen_pairs:
                    continue
                seen_pairs.add((i, j))
                n_cmp += 1
                if should_cancel and n_cmp % _PAIR_CHECK_EVERY == 0 and should_cancel():
                    return []
                s = _score_nodes(ai, node_obj[j])
                if s >= theta_request:
                    edges[(i, j)] = s
    adj: dict[int, dict[int, float]] = defaultdict(dict)
    for (i, j), s in edges.items():
        adj[i][j] = s
        adj[j][i] = s

    del seen_pairs

    # ---- STEP 5: constrained agglomeration in descending edge order (no chaining)
    parent = list(range(len(nodes)))
    members: dict[int, list[int]] = {k: [k] for k in range(len(nodes))}
    csize = list(node_size)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for step, ((i, j), s) in enumerate(sorted(edges.items(), key=lambda e: (-e[1], e[0]))):
        if should_cancel and step % _PAIR_CHECK_EVERY == 0 and should_cancel():
            return []
        if s < theta_cluster:
            break
        ri, rj = find(i), find(j)
        if ri == rj or csize[ri] + csize[rj] > cap:
            continue
        mi, mj = members[ri], members[rj]
        tot, ok = 0.0, True
        for a in mi:
            row = adj[a]
            for b in mj:
                sab = row.get(b, 0.0)
                if sab < theta_request:
                    ok = False
                    break
                tot += sab
            if not ok:
                break
        if not ok or tot / (len(mi) * len(mj)) < theta_cluster:
            continue
        keep, drop = (ri, rj) if ri < rj else (rj, ri)
        parent[drop] = keep
        members[keep] = members[keep] + members.pop(drop)
        csize[keep] += csize[drop]
    clusters = [sorted(members[r]) for r in sorted(members)]
    cluster_of = {}
    for ci, cl in enumerate(clusters):
        for k in cl:
            cluster_of[k] = ci

    # ---- tier-1 decision per spelling group: a proposal only when no other variant links to it
    hidden: set[int] = set()     # node k of groups emitted as auto and NOT sent to the LLM
    for k, nid in enumerate(nodes):
        if nid not in node_members:
            continue
        if not any(s >= theta_cluster for s in adj[k].values()):
            hidden.add(k)

    # ---- STEP 6: request components over the candidate graph at theta_request
    cparent = list(range(len(clusters)))

    def cfind(x):
        while cparent[x] != x:
            cparent[x] = cparent[cparent[x]]
            x = cparent[x]
        return x

    for (i, j) in edges:
        if i in hidden or j in hidden:
            continue
        ci, cj = cfind(cluster_of[i]), cfind(cluster_of[j])
        if ci != cj:
            cparent[max(ci, cj)] = min(ci, cj)
    comps: dict[int, list[int]] = defaultdict(list)
    for c in range(len(clusters)):
        comps[cfind(c)].append(c)

    def expand(k) -> list[_Addr]:
        nid = nodes[k]
        return [variants[i] for i in node_members.get(nid, [nid])]

    def records_of(k):
        nid = nodes[k]
        recs: set[int] = set()
        for i in node_members.get(nid, [nid]):
            recs |= variants[i].records
        return recs

    def sig_of(k) -> str:
        a = node_var[k]
        return " ".join(sorted(t for t in (a.inst or a.full_loose) if t not in _STRUCT))

    auto_groups: list[tuple[str, str, list[_Addr]]] = []   # (country slug, sort key, variants)
    for k in sorted(hidden):
        a = node_var[k]
        auto_groups.append((_slug(a.country), a.norm, [variants[i] for i in node_members[nodes[k]]]))

    cedges_by_root: Optional[dict[int, dict[tuple[int, int], float]]] = None   # built once, on demand

    units: list[tuple[str, str, list[tuple[list[_Addr], set]]]] = []   # (slug, sort key, [(variants, records)])
    for step, root in enumerate(sorted(comps)):
        if should_cancel and step % _CANCEL_EVERY == 0 and should_cancel():
            return []
        live = [c for c in comps[root] if any(k not in hidden for k in clusters[c])]
        if not live:
            continue
        cvars: dict[int, list[_Addr]] = {}
        crecs: dict[int, set] = {}
        for c in live:
            vs: list[_Addr] = []
            recs: set[int] = set()
            for k in clusters[c]:
                if k in hidden:
                    continue
                vs.extend(expand(k))
                recs |= records_of(k)
            cvars[c], crecs[c] = vs, recs
        n_var = sum(len(v) for v in cvars.values())
        if n_var < 2:
            continue
        slug = _slug(node_var[clusters[live[0]][0]].country)
        if n_var <= cap:
            chunks = [list(live)]
        else:
            # over-cap component: glue candidates in descending edge order while <= cap
            # (a candidate larger than cap is sliced by sorted spelling)
            if cedges_by_root is None:
                # per-component max score between two candidates, grouped by component root once
                # (one pass over the edges instead of one pass per over-cap component)
                cedges_by_root = defaultdict(dict)
                for (i, j), sc in edges.items():
                    if i in hidden or j in hidden:
                        continue
                    ci, cj = cluster_of[i], cluster_of[j]
                    if ci == cj:
                        continue
                    ce = cedges_by_root[cfind(ci)]
                    key = (min(ci, cj), max(ci, cj))
                    if sc > ce.get(key, 0.0):
                        ce[key] = sc
            cedges = cedges_by_root.get(root, {})
            cp = {c: c for c in live}
            csz = {c: len(cvars[c]) for c in live}
            cmem = {c: [c] for c in live}

            def cf(x):
                while cp[x] != x:
                    cp[x] = cp[cp[x]]
                    x = cp[x]
                return x

            for (ci, cj), _sc in sorted(cedges.items(), key=lambda e: (-e[1], e[0])):
                ri, rj = cf(ci), cf(cj)
                if ri == rj or csz[ri] + csz[rj] > cap:
                    continue
                keep, drop = (ri, rj) if ri < rj else (rj, ri)
                cp[drop] = keep
                csz[keep] += csz[drop]
                cmem[keep] = cmem[keep] + cmem.pop(drop)
            chunks = [sorted(cmem[r]) for r in sorted(cmem)]
        for ch in chunks:
            sort_key = min(sig_of(k) for c in ch for k in clusters[c] if k not in hidden)
            pieces: list[tuple[list[_Addr], set]] = []
            size = 0
            for c in ch:
                vs = cvars[c]
                if len(vs) > cap:
                    vs = sorted(vs, key=lambda v: (v.norm, v.raw))
                    for p0 in range(0, len(vs), cap):
                        piece = vs[p0:p0 + cap]
                        if pieces and size + len(piece) > cap:
                            units.append((slug, sort_key, pieces))
                            pieces, size = [], 0
                        pieces.append((piece, crecs[c]))
                        size += len(piece)
                else:
                    pieces.append((vs, crecs[c]))
                    size += len(vs)
            if pieces:
                units.append((slug, sort_key, pieces))
    units = [u for u in units if sum(len(p) for p, _r in u[2]) >= 2]

    # ---- first-fit packing per country in signature order
    out: list[dict] = []
    bins: dict[str, list[list]] = defaultdict(list)
    units.sort(key=lambda u: (u[0], u[1]))
    for slug, _s, chunk in units:
        nv = sum(len(p) for p, _r in chunk)
        for b in bins[slug]:
            if b[0] + nv <= cap:
                b[0] += nv
                b[1].append(chunk)
                break
        else:
            bins[slug].append([nv, [chunk]])
    for slug in sorted(bins):
        for bi, (_nv, chunks) in enumerate(bins[slug]):
            key = f"{slug}~{bi}"
            mem = []
            for chunk in chunks:
                for piece, recs in chunk:
                    mem.append(_cand(key, len(mem), piece, uid_of, 2, recs))
            out.append({"key": key, "members": mem})
    # one block per tier-1 group (a block = one proposal; no LLM request is made for it)
    for ai, (slug, _nm, vs) in enumerate(sorted(auto_groups, key=lambda g: (g[0], g[1]))):
        key = f"{slug}~a{ai}"
        out.append({"key": key, "auto": [_cand(key, 0, vs, uid_of, 1)]})
    return out
