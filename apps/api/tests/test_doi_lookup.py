"""Reverse DOI lookup — precision-first, fully offline.

A wrong DOI is far worse than a missing one: Smart Merge treats a DOI as determinative and
the enrichment passes that follow import the abstract / keywords / affiliations of the
work the DOI points to. These tests pin the abstention rules (preprints, wrong publication
types, ambiguous ties, duplicates, erratum/comment/reply look-alikes, changed numbers) and
the HTTP behaviour (OpenAlex circuit breaker, no long sleeps) without any network access:
every request goes through ``api_utils._http_get``, which the tests replace.

Fixtures in ``fixtures/doi_lookup_cases.json`` are hand-picked from recorded Crossref
responses (title query, rows=10); only the fields the lookup reads were kept.
"""

import asyncio
import copy
import csv
import json
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

# bibex_core lives in <repo>/packages; make the tests exercise the repo copy rather than any
# stale wheel installed into site-packages (same approach as test_cr_normalize.py).
_PACKAGES = _API_ROOT.parents[1] / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))
    for _mod in [m for m in sys.modules if m == "bibex_core" or m.startswith("bibex_core.")]:
        sys.modules.pop(_mod, None)

from bibex_core.modules import api_utils as A  # noqa: E402

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "doi_lookup_cases.json").read_text(encoding="utf-8")
)["cases"]


# ── Test doubles ────────────────────────────────────────────────────────────────
class Resp:
    def __init__(self, payload=None, code=200, headers=None):
        self._payload, self.status_code, self.headers = payload, code, headers or {}
        self.ok = 200 <= code < 300

    def json(self):
        return self._payload


class Net:
    """Replacement for api_utils._http_get that records calls and serves canned answers."""

    def __init__(self, crossref=None, openalex=None, singles=None, crossref_resp=None, openalex_resp=None):
        self.crossref = crossref if crossref is not None else []          # list or callable(params)->list
        self.openalex = openalex if openalex is not None else []          # list of OpenAlex works
        self.singles = singles or {}                                      # doi -> Crossref item
        self.crossref_resp, self.openalex_resp = crossref_resp, openalex_resp   # force a Resp
        self.calls = []

    def kinds(self):
        return [k for k, _u, _p in self.calls]

    def __call__(self, url, params=None, headers=None):
        params = params or {}
        if "api.openalex.org" in url:
            self.calls.append(("openalex", url, params))
            if self.openalex_resp is not None:
                return self.openalex_resp
            return Resp({"results": self.openalex})
        self.calls.append(("crossref", url, params))
        if url.rstrip("/").endswith("/works"):
            if self.crossref_resp is not None:
                return self.crossref_resp
            items = self.crossref(params) if callable(self.crossref) else self.crossref
            return Resp({"message": {"items": items}})
        doi = url.split("/works/", 1)[1].lower()
        item = self.singles.get(doi)
        return Resp({"message": item}, 200) if item else Resp({}, 404)


@pytest.fixture
def net(monkeypatch):
    def install(**kw):
        n = Net(**kw)
        monkeypatch.setattr(A, "_http_get", n)
        return n
    return install


def case(name):
    c = copy.deepcopy(FIXTURES[name])
    return c["rec"], c["items"], c["truth"]


def item(doi, title, *, type="journal-article", year=2024, container="Journal of Tests", issn=("1234-5678",),
         authors=(("Smith", "John"),), subtitle=None, relation=None, volume=None, page=None, **extra):
    it = {"DOI": doi, "title": [title], "type": type, "issued": {"date-parts": [[year]]}}
    if container:
        it["container-title"] = [container]
    if issn:
        it["ISSN"] = list(issn)
    it["author"] = [{"family": f, "given": g} for f, g in authors]
    if subtitle:
        it["subtitle"] = [subtitle]
    if relation:
        it["relation"] = relation
    if volume:
        it["volume"] = volume
    if page:
        it["page"] = page
    it.update(extra)
    return it


def lookup(rec, items, net, **kw):
    n = net(crossref=items)
    state = kw.pop("state", None) or A.DoiLookupState()
    return A.lookup_doi(rec, state=state, **kw), n


REC = {"TI": "ASSESSING LARGE LANGUAGE MODELS FOR CLINICAL TRIAL RECRUITMENT", "AU": "SMITH J; DOE A",
       "PY": "2024", "SO": "JOURNAL OF TESTS", "SN": "1234-5678", "UID": "u1"}
CAND = item("10.1000/Test.1", "Assessing large language models for clinical trial recruitment",
            authors=(("Smith", "John"), ("Doe", "Alice")))


# ── Helpers / query construction ────────────────────────────────────────────────
def test_doi_helpers():
    assert A.clean_doi("https://doi.org/10.1000/ABC.Def/") == "10.1000/abc.def"
    assert A.clean_doi("doi: 10.1000/X.") == "10.1000/x"
    assert A.clean_doi("not a doi") == ""
    # the comparison key folds separators exactly like smart_merger.normalize_doi ...
    assert A.doi_key("10.4103/JGID.jgid_12_19") == A.doi_key("10.4103/jgid.jgid-12-19")
    # ... while the written form keeps the real characters
    assert A.clean_doi("10.4103/jgid.jgid_12_19") == "10.4103/jgid.jgid_12_19"
    try:
        from services.smart_merger import normalize_doi
    except Exception:                       # pragma: no cover - smart_merger unavailable
        return
    for raw in ("https://doi.org/10.1234/ABC", "10.4103/jgid.jgid_12_19", "doi:10.1/x/", "10.1/y."):
        assert A.doi_key(raw) == normalize_doi(raw)


def test_crossref_query_shape():
    p = A._crossref_query("<i>Deep</i> learning &amp; CO<sub>2</sub>  models", "VAN DER BERG J; X Y", "2024.0")
    assert p["rows"] == 10
    assert p["select"] == A.CROSSREF_SELECT
    assert p["query.bibliographic"] == "Deep learning & CO2 models berg 2024"
    assert "mailto" not in p                                  # the caller adds it when configured
    # surname / year are omitted when unknown; the title is capped at 300 characters
    p2 = A._crossref_query("T" * 400, None, None)
    assert p2["query.bibliographic"] == "T" * 300


def test_cache_is_per_run_and_keyed_by_exact_request(net):
    n = net(crossref=[CAND])
    state = A.DoiLookupState()
    for _ in range(2):
        assert A.lookup_doi(REC, state=state).outcome == "filled"
    assert n.kinds() == ["crossref"]                          # second lookup served from the run cache
    A.lookup_doi(REC, state=A.DoiLookupState())               # a new run never reuses it
    assert n.kinds() == ["crossref", "crossref"]


# ── Accepting a match ───────────────────────────────────────────────────────────
def test_exact_match(net):
    rec, items, truth = case("exact")
    d, n = lookup(rec, items, net)
    assert d.outcome == "filled" and d.doi == truth.lower()
    assert d.source == "crossref" and d.cand_type == "journal-article"
    assert n.kinds() == ["crossref"]                          # OpenAlex is never asked when Crossref is enough
    assert n.calls[0][2]["rows"] == 10 and "select" in n.calls[0][2]


def test_wos_uppercase_title_and_doi_written_lowercase(net):
    cand = item("10.1000/ABC.Def", "Assessing Large Language Models for Clinical Trial Recruitment",
                authors=(("Smith", "John"),))
    d, _ = lookup(REC, [cand], net)
    assert d.outcome == "filled" and d.doi == "10.1000/abc.def"


def test_subtitle_in_separate_crossref_field(net):
    rec, items, truth = case("subtitle_in_crossref_field")
    assert items[0]["subtitle"]
    d, _ = lookup(rec, items, net)
    assert d.outcome == "filled" and d.doi == truth.lower() and d.score >= 1.0


def test_subtitle_omitted_in_record_needs_author_and_year(net):
    # Crossref carries the subtitle inside the title string; the record has the main title only.
    cand = item("10.1000/sub.1", "Large language models in clinical practice: opportunities and challenges for hospitals",
                authors=(("Smith", "John"),))
    rec = {"TI": "LARGE LANGUAGE MODELS IN CLINICAL PRACTICE", "AU": "SMITH J", "PY": "2024",
           "SO": "JOURNAL OF TESTS", "SN": "1234-5678"}
    d, _ = lookup(rec, [cand], net)
    assert d.outcome == "filled" and "subtitle" in d.reason and d.score < 1.0
    # the same shortcut also works the other way round (record has the subtitle, Crossref does not)
    cand_short = item("10.1000/sub.2", "Large language models in clinical practice", authors=(("Smith", "John"),))
    rec_long = {**rec, "TI": "LARGE LANGUAGE MODELS IN CLINICAL PRACTICE: OPPORTUNITIES AND CHALLENGES FOR HOSPITALS"}
    assert lookup(rec_long, [cand_short], net)[0].outcome == "filled"
    # it is only allowed with a known year and agreeing authors ...
    assert lookup({**rec, "PY": ""}, [cand], net)[0].doi is None
    assert lookup({**rec, "AU": "ZZZQ X"}, [cand], net)[0].doi is None
    # ... and the shared main part must have >= 4 content tokens
    cand3 = item("10.1000/sub.3", "Language models in medicine: a new era of patient care", authors=(("Smith", "John"),))
    assert lookup({**rec, "TI": "LANGUAGE MODELS IN MEDICINE"}, [cand3], net)[0].doi is None
    # an omitted 'Part II' style tail is never a subtitle
    cand4 = item("10.1000/sub.4", "Large language models in clinical practice. Part II", authors=(("Smith", "John"),))
    assert lookup(rec, [cand4], net)[0].doi is None


def test_trailing_bracketed_translation_is_ignored(net):
    rec = {**REC, "TI": REC["TI"] + "; [AVALIACAO DE MODELOS DE LINGUAGEM PARA RECRUTAMENTO]"}
    d, _ = lookup(rec, [CAND], net)
    assert d.outcome == "filled"


def test_spelling_variant_and_score_penalty(net):
    base, _ = lookup({**REC, "TI": "ASSESSING LARGE LANGUAGE MODELS FOR CLINICAL TRIAL RECRUITMENT"}, [CAND], net)
    cand = item("10.1000/var.1", "Assessing large language model for clinical trial recruitments",
                authors=(("Smith", "John"),))
    d, _ = lookup(REC, [cand], net)
    assert d.outcome == "filled" and d.score < base.score      # variants are accepted but cost score


# ── Preprints ───────────────────────────────────────────────────────────────────
def test_preprint_first_journal_present_journal_wins(net):
    rec, items, truth = case("preprint_first")
    assert items[0]["type"] == "posted-content"
    d, _ = lookup(rec, items, net)
    assert d.doi == truth.lower() and d.cand_type == "journal-article"


def test_preprint_only_is_not_written(net):
    rec, items, truth = case("preprint_only")
    d, _ = lookup(rec, items, net)
    assert d.outcome == "preprint_only" and d.doi is None
    assert d.preprint_doi == "10.2139/ssrn.4718891"
    assert "do not use as the journal DOI" in d.warning


def test_preprint_follows_is_preprint_of_to_published_version(net):
    c = copy.deepcopy(FIXTURES["preprint_follows_to_published"])
    pub = c["published_version"]
    n = net(crossref=c["items"], singles={pub["DOI"].lower(): pub})
    d = A.lookup_doi(c["rec"], state=A.DoiLookupState())
    assert d.outcome == "filled" and d.doi == c["truth"].lower()
    assert d.preprint_doi and d.preprint_doi != d.doi
    assert [u.rsplit("/works/", 1)[-1].lower() for k, u, _ in n.calls if "/works/" in u] == [pub["DOI"].lower()]


def test_unverifiable_published_version_stays_preprint_only(net):
    c = copy.deepcopy(FIXTURES["preprint_follows_to_published"])
    # (a) the published DOI does not resolve
    net(crossref=c["items"])
    assert A.lookup_doi(c["rec"], state=A.DoiLookupState()).outcome == "preprint_only"
    # (b) it resolves to a different work: the FULL rules reject it
    pub = copy.deepcopy(c["published_version"])
    pub["title"] = ["A completely different study of unrelated things"]
    net(crossref=c["items"], singles={pub["DOI"].lower(): pub})
    d = A.lookup_doi(c["rec"], state=A.DoiLookupState())
    assert d.outcome == "preprint_only" and d.doi is None


def test_record_that_is_itself_a_preprint_may_get_a_preprint_doi(net):
    pre = item("10.48550/arxiv.2401.00001", "Assessing large language models for clinical trial recruitment",
               type="posted-content", container="arXiv", issn=(), authors=(("Smith", "John"),))
    rec = {**REC, "SO": "ARXIV", "SN": ""}
    d, _ = lookup(rec, [pre], net)
    assert d.outcome == "filled" and d.doi == "10.48550/arxiv.2401.00001"
    d2, _ = lookup(REC, [pre], net)                          # a journal record must not get it
    assert d2.outcome == "preprint_only"


# ── Publication types ───────────────────────────────────────────────────────────
def test_peer_review_and_component_rejected_original_wins(net):
    for name in ("peer_review_first", "component_first"):
        rec, items, truth = case(name)
        assert items[0]["type"] in ("peer-review", "component")
        d, _ = lookup(rec, items, net)
        assert d.doi == truth.lower(), name


def test_only_a_wrong_type_matches_reports_type_rejected(net):
    cand = item("10.1000/test.1.s001", REC["TI"].title(), type="component", authors=(("Smith", "John"),))
    d, _ = lookup(REC, [cand], net)
    assert d.outcome == "type_rejected" and d.doi is None


def test_book_chapter_loses_to_journal_article(net):
    rec, items, truth = case("book_chapter_first")
    assert items[0]["type"] == "book-chapter"
    d, _ = lookup(rec, items, net)
    assert d.doi == truth.lower()


def test_suspicious_doi_shape_is_only_a_logged_signal(net):
    cand = item("10.1000/test.1/review2", REC["TI"].title(), authors=(("Smith", "John"),))
    d, _ = lookup(REC, [cand], net)
    assert d.outcome == "filled" and "signal" in d.reason     # logged, not a rejection rule


# ── Ties, duplicates ────────────────────────────────────────────────────────────
def test_two_different_journal_dois_with_equal_score_abstain(net):
    rec, items, _ = case("ambiguous_two_journal_dois")
    d, _ = lookup(rec, items, net)
    assert d.outcome == "ambiguous" and d.doi is None
    assert "10.57239/pjlss-2025-23.1.0018" in d.reason


def test_issn_breaks_a_tie_between_two_journal_dois(net):
    a = item("10.1000/a", CAND["title"][0], container="Journal of Tests", issn=("1234-5678",),
             authors=(("Smith", "John"),))
    b = item("10.1000/b", CAND["title"][0], container="Other Journal", issn=("8765-4321",),
             authors=(("Smith", "John"),))
    d, _ = lookup(REC, [b, a], net)             # b conflicts on ISSN -> only a remains
    assert d.doi == "10.1000/a"
    rec_no_issn = {k: v for k, v in REC.items() if k != "SN"}
    d2, _ = lookup(rec_no_issn, [b, a], net)    # container name still separates them (penalty vs bonus)
    assert d2.doi == "10.1000/a"


def test_duplicate_doi_against_existing_rows(net):
    d, _ = lookup(REC, [CAND], net, existing={"10.1000/test.1": "uid-42"})
    assert d.outcome == "duplicate" and d.doi is None and d.duplicate_of == "uid-42"
    assert "Smart Merge did not merge" in d.warning
    # a set works too, and the comparison is separator-folded like Smart Merge
    cand = item("10.4103/jgid.jgid_12_19", CAND["title"][0], authors=(("Smith", "John"),))
    d2, _ = lookup(REC, [cand], net, existing={A.doi_key("10.4103/jgid.jgid-12-19")})
    assert d2.outcome == "duplicate"


# ── Look-alikes that must NOT take the original paper's DOI ─────────────────────
@pytest.mark.parametrize("prefix", ["ERRATUM TO: ", "CORRECTION: ", "COMMENT ON: ", "REPLY TO: ",
                                    "REVIEW OF: ", "RESPONSE TO: ", "RETRACTION NOTE: "])
def test_marker_prefixes_never_take_the_original_doi(net, prefix):
    rec, items, truth = case("exact")
    rec["TI"] = prefix + rec["TI"]
    d, _ = lookup(rec, items, net)
    assert d.doi is None


def test_candidate_side_marker_is_rejected_too(net):
    cand = item("10.1000/e.1", "Erratum: " + CAND["title"][0], authors=(("Smith", "John"),))
    d, _ = lookup(REC, [cand], net)
    assert d.doi is None


@pytest.mark.parametrize("suffix", [". PART II", ": PART 2", " - STUDY 2", " (PHASE B)"])
def test_series_designators_never_take_the_original_doi(net, suffix):
    rec, items, truth = case("exact")
    rec["TI"] = rec["TI"] + suffix
    d, _ = lookup(rec, items, net)
    assert d.doi is None


def test_changed_digit_is_rejected_and_roman_equals_arabic(net):
    cand = item("10.1000/n.1", "Performance of GPT-4 on clinical vignettes: study 2 of ten", authors=(("Smith", "John"),))
    rec = {**REC, "TI": "PERFORMANCE OF GPT-4 ON CLINICAL VIGNETTES: STUDY 2 OF TEN"}
    assert lookup(rec, [cand], net)[0].outcome == "filled"
    assert lookup({**rec, "TI": rec["TI"].replace("GPT-4", "GPT-3")}, [cand], net)[0].doi is None
    assert lookup({**rec, "TI": rec["TI"].replace("STUDY 2", "STUDY II")}, [cand], net)[0].outcome == "filled"
    assert lookup({**rec, "TI": rec["TI"].replace("STUDY 2", "STUDY 3")}, [cand], net)[0].doi is None


def test_documented_limitation_single_letter_antonym_is_not_detected():
    # Antonyms are NOT a goal of the title comparison. Only a one-letter difference inside
    # an alphabetic ASCII token of >= 5 characters is tolerated as a spelling variant, which
    # is what lets 'typical'/'atypical' through. Words such as with/without,
    # increase/decrease or before/after differ by more than that and are rejected.
    c = A.Candidate(doi="10.1/x", title="Typical presentation of long covid in adults")
    assert A.title_match("Atypical presentation of long covid in adults", c)[0] is True
    c2 = A.Candidate(doi="10.1/y", title="Increase in attendance after the reform of schools")
    assert A.title_match("Decrease in attendance after the reform of schools", c2)[0] is False
    c3 = A.Candidate(doi="10.1/z", title="Learning with tablets in primary schools")
    assert A.title_match("Learning without tablets in primary schools", c3)[0] is False


# ── Journal evidence ────────────────────────────────────────────────────────────
def test_issn_conflict_rejected_and_overlap_scores_higher(net):
    d, _ = lookup({**REC, "SN": "1111-2222"}, [CAND], net)
    assert d.doi is None and "ISSN" in d.reason
    base, _ = lookup({k: v for k, v in REC.items() if k not in ("SN", "SO")}, [CAND], net)
    withissn, _ = lookup(REC, [CAND], net)
    assert withissn.score > base.score
    # print ISSN of the record, electronic ISSN of the candidate list
    cand = item("10.1000/e.2", CAND["title"][0], issn=("2222-3333", "1234-5678"), authors=(("Smith", "John"),))
    assert lookup(REC, [cand], net)[0].outcome == "filled"


def test_container_name_mismatch_alone_is_only_a_penalty(net):
    rec = {k: v for k, v in REC.items() if k != "SN"}
    ok, _ = lookup(rec, [CAND], net)
    off, _ = lookup({**rec, "SO": "PROCEEDINGS OF AN ENTIRELY UNRELATED CONFERENCE"}, [CAND], net)
    assert off.outcome == "filled" and off.score < ok.score


def test_volume_and_first_page_add_score(net):
    cand = item("10.1000/v.1", CAND["title"][0], authors=(("Smith", "John"),), volume="12", page="101-110")
    plain, _ = lookup(REC, [cand], net)
    both, _ = lookup({**REC, "VL": "12", "BP": "101"}, [cand], net)
    assert both.score == pytest.approx(plain.score + 0.02)


# ── Year ────────────────────────────────────────────────────────────────────────
def test_year_window_is_plus_minus_one_over_all_dates(net):
    for y, ok in ((2023, True), (2025, True), (2026, False), (2021, False)):
        cand = item("10.1000/y.1", CAND["title"][0], year=y, authors=(("Smith", "John"),))
        assert (lookup(REC, [cand], net)[0].outcome == "filled") is ok, y


def test_year_from_published_online_counts(net):
    cand = item("10.1000/po.1", CAND["title"][0], year=2026, authors=(("Smith", "John"),),
                **{"published-online": {"date-parts": [[2024, 5, 6]]}})
    assert lookup(REC, [cand], net)[0].outcome == "filled"


def test_candidate_without_any_year_is_rejected(net):
    cand = item("10.1000/ny.1", CAND["title"][0], authors=(("Smith", "John"),))
    cand["issued"] = {"date-parts": [[None]]}
    d, _ = lookup(REC, [cand], net)
    assert d.doi is None and "no year" in d.reason


def test_record_without_year_needs_author_agreement_and_journal(net):
    rec = {**REC, "PY": ""}
    assert lookup(rec, [CAND], net)[0].outcome == "filled"                       # authors + ISSN agree
    weak = {k: v for k, v in rec.items() if k not in ("SN", "SO")}
    assert lookup(weak, [CAND], net)[0].doi is None                              # no journal evidence
    assert lookup({**rec, "AU": "ZZZQ X"}, [CAND], net)[0].doi is None           # authors disagree


# ── Authors ─────────────────────────────────────────────────────────────────────
def test_record_without_authors_needs_journal_evidence(net):
    rec = {**REC, "AU": "", "AF": ""}
    assert lookup(rec, [CAND], net)[0].outcome == "filled"
    weak = {k: v for k, v in rec.items() if k not in ("SN", "SO")}
    assert lookup(weak, [CAND], net)[0].doi is None


def test_candidate_without_authors_needs_issn_or_container(net):
    cand = item("10.1000/na.1", CAND["title"][0], authors=())
    assert lookup(REC, [cand], net)[0].outcome == "filled"                       # ISSN equal
    weak = {k: v for k, v in REC.items() if k not in ("SN",)}
    assert lookup(weak, [cand], net)[0].outcome == "filled"                      # container name equal (>= 0.9)
    assert lookup({**weak, "SO": "SOME OTHER JOURNAL"}, [cand], net)[0].doi is None


def test_no_authors_on_either_side_is_never_enough(net):
    cand = item("10.1000/nn.1", CAND["title"][0], authors=())
    assert lookup({**REC, "AU": "", "AF": ""}, [cand], net)[0].doi is None


def test_short_generic_titles_need_authors_and_journal(net):
    cand = item("10.1000/g.1", "Editorial", authors=(("Smith", "John"),))
    rec = {"TI": "EDITORIAL", "AU": "SMITH J", "PY": "2024", "SO": "JOURNAL OF TESTS", "SN": "1234-5678"}
    assert lookup(rec, [cand], net)[0].outcome == "filled"
    assert lookup({**rec, "SN": "", "SO": ""}, [cand], net)[0].doi is None
    assert lookup({**rec, "AU": ""}, [cand], net)[0].doi is None
    # a short (< 5 content tokens) title is generic as well
    cand2 = item("10.1000/g.2", "Prompt engineering basics", authors=(("Smith", "John"),))
    assert lookup({**rec, "TI": "PROMPT ENGINEERING BASICS", "SN": "", "SO": ""}, [cand2], net)[0].doi is None


def test_common_surname_needs_the_initial(net):
    cand = item("10.1000/w.1", CAND["title"][0], authors=(("Wang", "Xiao"), ("Li", "Ming")))
    rec = {**REC, "AU": "WANG L"}
    assert lookup(rec, [cand], net)[0].doi is None                               # Wang, X != Wang, L
    assert lookup({**rec, "AU": "WANG X"}, [cand], net)[0].outcome == "filled"
    assert lookup({**rec, "AU": "WANG"}, [cand], net)[0].outcome == "filled"     # no initial on the record side
    assert lookup({**rec, "AU": "ZHANG Q; LI M"}, [cand], net)[0].outcome == "filled"   # any of the first 3


def test_author_name_forms():
    pa = A.parse_record_authors
    assert pa("VAN DER BERG J")[0].fam == frozenset({"berg"})
    assert pa("de la Cruz, Maria")[0].fam == frozenset({"cruz"}) and pa("de la Cruz, Maria")[0].initial == "m"
    assert pa("zhao (赵勇), Y")[0].fam == frozenset({"zhao"})
    assert pa("SALGADO GRANDA BS")[0].fam == frozenset({"salgado", "granda"})
    assert pa("BURTCH, GORDON (55502077800); LEE, DOKYUN (57257898700)")[1].family == "lee"
    assert pa("Müller, K")[0].fam == pa("MULLER K")[0].fam
    assert len(pa("ALPHA A; BETA B; GAMMA C; DELTA D", limit=3)) == 3
    cand = A.Candidate(doi="10.1/a", authors=[("Müller", "Karl"), ("Garcia-Lopez", "Ana")])
    assert A._authors_agree(pa("MULLER K"), cand)
    assert A._authors_agree(pa("LOPEZ A"), cand)
    assert not A._authors_agree(pa("MULLER P"), cand)


# ── Title comparison on non-Latin and damaged text ──────────────────────────────
def test_title_comparison_non_latin_accents_markup_and_mojibake():
    def ok(rec_title, cand_title, **kw):
        return A.title_match(rec_title, A.Candidate(doi="10.1/t", title=cand_title, **kw))[0]
    assert ok("生成式人工智能在教育中的应用研究", "生成式人工智能在教育中的应用研究")
    assert not ok("生成式人工智能在教育中的应用研究", "生成式人工智能在医学中的应用研究")
    assert ok("ГЕНЕРАТИВНЫЙ ИСКУССТВЕННЫЙ ИНТЕЛЛЕКТ В ОБРАЗОВАНИИ", "Генеративный искусственный интеллект в образовании")
    assert ok("MULLER'S CAFE UBER DIE GUTE", "Müller’s Café über die Güte")
    assert ok("EFFECTS OF CO2 ON <I>E. COLI</I> GROWTH", "Effects of CO<sub>2</sub> on <i>E. coli</i> growth")
    assert ok("ENHANCING EFL SPEAKING WITH CHATGPT�S VOICE PROMPTS", "Enhancing EFL speaking with ChatGPT's voice prompts")
    assert ok("E-LEARNING ADOPTION IN SCHOOLS", "Elearning adoption in schools")
    assert ok("TYPE II DIABETES SCREENING IN ADULTS", "Type 2 diabetes screening in adults")


# ── Sources, circuit breaker, no long sleeps ────────────────────────────────────
def test_openalex_is_only_a_fallback_when_crossref_found_nothing(net):
    work = {"doi": "https://doi.org/10.1000/OA.1", "title": CAND["title"][0], "publication_year": 2024,
            "type": "article", "authorships": [{"author": {"display_name": "John Smith"}}],
            "primary_location": {"source": {"display_name": "Journal of Tests", "issn": ["1234-5678"]}}}
    n = net(crossref=[], openalex=[work])
    d = A.lookup_doi(REC, state=A.DoiLookupState(), openalex_key="k123")
    assert d.outcome == "filled" and d.doi == "10.1000/oa.1" and d.source == "openalex"
    assert n.kinds() == ["crossref", "openalex"]
    params = n.calls[1][2]
    assert params["api_key"] == "k123" and "mailto" not in params
    assert params["filter"] == "publication_year:2023|2024|2025" and params["per_page"] == 5
    assert "primary_location" in params["select"]
    # mailto instead of a key; no year -> no filter
    n2 = net(crossref=[], openalex=[work])
    A.lookup_doi({**REC, "PY": ""}, email="me@example.org", state=A.DoiLookupState())
    assert n2.calls[1][2]["mailto"] == "me@example.org" and "filter" not in n2.calls[1][2]


def test_openalex_candidates_go_through_the_same_rules(net):
    work = {"doi": "https://doi.org/10.1000/oa.2", "title": CAND["title"][0], "publication_year": 2024,
            "type": "preprint", "authorships": [{"author": {"display_name": "John Smith"}}], "primary_location": {}}
    net(crossref=[], openalex=[work])
    assert A.lookup_doi(REC, state=A.DoiLookupState()).outcome == "preprint_only"
    work["type"] = "article"
    work["authorships"] = [{"author": {"display_name": "Maria Garcia"}}]
    net(crossref=[], openalex=[work])
    assert A.lookup_doi(REC, state=A.DoiLookupState()).doi is None


def test_openalex_hard_429_trips_the_circuit_breaker_without_sleeping(net, monkeypatch):
    slept = []
    monkeypatch.setattr(A.time, "sleep", lambda s: slept.append(s))
    resp = Resp({"error": "Insufficient budget"}, 429, {"Retry-After": "61946", "X-RateLimit-Remaining": "0"})
    n = net(crossref=[], openalex_resp=resp)
    state = A.DoiLookupState()
    t0 = time.monotonic()
    decisions = [A.lookup_doi({**REC, "UID": f"u{i}"}, state=state) for i in range(5)]
    assert time.monotonic() - t0 < 2.0 and slept == []
    assert n.kinds().count("openalex") == 1                  # called once, then skipped for the rest of the run
    assert "openalex" in state.exhausted
    assert all(d.outcome == "no_match" for d in decisions)   # exhausted is not an error of the record
    assert "openalex" in decisions[-1].reason


def test_openalex_low_budget_header_exhausts_after_that_response(net):
    work = {"doi": "https://doi.org/10.1000/oa.3", "title": CAND["title"][0], "publication_year": 2024,
            "type": "article", "authorships": [{"author": {"display_name": "John Smith"}}], "primary_location": {}}
    n = net(crossref=[], openalex_resp=Resp({"results": [work]}, 200, {"X-RateLimit-Remaining": "5"}))
    state = A.DoiLookupState()
    first = A.lookup_doi(REC, state=state)
    assert first.outcome == "filled"                          # the response itself is still used
    assert "openalex" in state.exhausted
    A.lookup_doi({**REC, "TI": "SOMETHING ELSE ENTIRELY DIFFERENT HERE TODAY"}, state=state)
    assert n.kinds().count("openalex") == 1


def test_crossref_hard_429_is_an_api_error_not_a_miss(net, monkeypatch):
    slept = []
    monkeypatch.setattr(A.time, "sleep", lambda s: slept.append(s))
    n = net(crossref_resp=Resp({}, 429, {"Retry-After": "3600"}), openalex=[])
    state = A.DoiLookupState()
    d = A.lookup_doi(REC, state=state)
    assert d.outcome == "api_error" and slept == []
    assert "crossref" in state.exhausted
    assert n.kinds() == ["crossref", "openalex"]              # OpenAlex still gets its chance


def test_a_source_that_keeps_failing_is_given_up_for_the_run(net, monkeypatch):
    slept = []
    monkeypatch.setattr(A.time, "sleep", lambda s: slept.append(s))
    n = net(crossref_resp=Resp({}, 500), openalex_resp=Resp({}, 500))
    state = A.DoiLookupState()
    outcomes = [A.lookup_doi({**REC, "TI": f"A DISTINCT TITLE NUMBER {i} ABOUT NOTHING AT ALL"}, state=state).outcome
                for i in range(A.MAX_FAIL_STREAK + 4)]
    assert set(outcomes) == {"api_error"}
    assert "crossref" in state.exhausted
    assert n.kinds().count("crossref") == A.MAX_FAIL_STREAK     # no further requests once given up


def test_short_retry_after_is_honoured_once(net, monkeypatch):
    slept = []
    monkeypatch.setattr(A.time, "sleep", lambda s: slept.append(s))
    answers = iter([Resp({}, 429, {"Retry-After": "3"}), Resp({"message": {"items": [CAND]}})])
    calls = []

    def stub(url, params=None, headers=None):
        calls.append(url)
        return next(answers)
    monkeypatch.setattr(A, "_http_get", stub)
    d = A.lookup_doi(REC, state=A.DoiLookupState())
    assert d.outcome == "filled" and slept == [3.0] and len(calls) == 2


def test_get_with_retry_gives_up_on_a_long_retry_after(monkeypatch):
    slept, calls = [], []
    monkeypatch.setattr(A.time, "sleep", lambda s: slept.append(s))

    def stub(url, params=None, headers=None):
        calls.append(url)
        return Resp({}, 429, {"Retry-After": "100"})
    monkeypatch.setattr(A, "_http_get", stub)
    r = A._get_with_retry("https://api.example.org/x")
    assert r.status_code == 429 and len(calls) == 1 and slept == []
    # a short one is slept off (<= MAX_RETRY_AFTER) and retried
    answers = iter([Resp({}, 429, {"Retry-After": "2"}), Resp({"ok": 1}, 200)])
    monkeypatch.setattr(A, "_http_get", lambda url, params=None, headers=None: next(answers))
    assert A._get_with_retry("https://api.example.org/x").status_code == 200 and slept == [2.0]


def test_openalex_key_is_applied_to_every_openalex_request_only(monkeypatch):
    seen = []

    class R:
        status_code, headers = 200, {}

    monkeypatch.setattr(A.requests, "get", lambda url, params=None, headers=None, timeout=None: seen.append((url, params)) or R())
    A.set_openalex_api_key("secret-key")
    try:
        A._http_get("https://api.openalex.org/works/doi:10.1/x", None, None)
        A._http_get("https://api.openalex.org/works/doi:10.1/x?select=authorships", {"mailto": "a@b.c"}, None)
        A._http_get("https://api.crossref.org/works", {"rows": 1}, None)
    finally:
        A.set_openalex_api_key("")
    assert seen[0][1] == {"api_key": "secret-key"}
    assert seen[1][1] == {"mailto": "a@b.c", "api_key": "secret-key"}
    assert seen[2][1] == {"rows": 1}
    A._http_get("https://api.openalex.org/works/doi:10.1/x", None, None)     # key cleared again
    assert seen[3][1] is None


def test_resolve_doi_wrapper_is_backwards_compatible(net):
    net(crossref=[CAND])
    assert A.resolve_doi(REC["TI"], REC["AU"], REC["PY"], crossref_email="me@example.org") == "10.1000/test.1"
    assert A.resolve_doi("   ") is None
    net(crossref=[])
    assert A.resolve_doi(REC["TI"], REC["AU"], REC["PY"]) is None


# ═══ Enricher level ═════════════════════════════════════════════════════════════
from services import enricher  # noqa: E402


class Ctx:
    """Stub of jobs.runner.JobContext."""

    def __init__(self):
        self.lines, self.status, self.progress_values, self.cancelled = [], [], [], False

    def log(self, line, *, key=None, params=None):
        self.lines.append(line)
        if key is not None:
            self.status.append((key, params))

    def progress(self, value):
        self.progress_values.append(value)


def _rows_for_enricher():
    """(dataframe, crossref answers keyed by a title prefix, expectations) from the fixtures."""
    exact_rec, exact_items, exact_truth = case("exact")
    exact_items[0]["DOI"] = exact_items[0]["DOI"].upper()           # Crossref hands out mixed case
    pf_rec, pf_items, pf_truth = case("preprint_first")
    amb_rec, amb_items, _ = case("ambiguous_two_journal_dois")
    po_rec, po_items, _ = case("preprint_only")
    comp_rec, comp_items, comp_truth = case("component_first")
    answers = {r["TI"][:25]: items for r, items in
               ((exact_rec, exact_items), (pf_rec, pf_items), (amb_rec, amb_items),
                (po_rec, po_items), (comp_rec, comp_items))}
    cols = ("UID", "TI", "AU", "AF", "PY", "SO", "JI", "J9", "SN", "EI", "VL", "BP", "DT", "AB")
    rows = []
    for r in (exact_rec, pf_rec, amb_rec, po_rec, comp_rec):
        rows.append({**{c: "" for c in cols}, **r, "UID": f"u{len(rows)}", "DI": ""})
    rows.append({**{c: "" for c in cols}, "UID": "nomatch", "TI": "A TITLE THAT NO SOURCE EVER HEARD OF BEFORE", "PY": "2024", "DI": ""})
    # the journal DOI of the preprint-first record already sits on another row (a duplicate Smart Merge missed)
    rows.append({**{c: "" for c in cols}, "UID": "holder", "TI": "SOME OTHER ROW", "DI": "https://doi.org/" + pf_truth.upper()})
    df = pd.DataFrame(rows)
    return df, answers, {"exact": exact_truth.lower(), "component": comp_truth.lower(), "pf_truth": pf_truth.lower()}


def _install_crossref_by_title(monkeypatch, answers):
    def handler(params):
        q = params.get("query.bibliographic", "")
        for prefix, items in answers.items():
            if q.upper().startswith(prefix.upper()):
                return items
        return []
    n = Net(crossref=handler)
    monkeypatch.setattr(A, "_http_get", n)
    return n


def test_doi_pass_end_to_end(tmp_path, monkeypatch):
    df, answers, exp = _rows_for_enricher()
    _install_crossref_by_title(monkeypatch, answers)
    ctx, stats = Ctx(), enricher._new_doi_stats()
    asyncio.run(enricher._doi_pass(ctx, df, out_dir=tmp_path, stats=stats))

    # written lowercase, only into the rows that were matched with confidence
    assert df.loc[df["UID"] == df.iloc[0]["UID"], "DI"].iloc[0] == exp["exact"]
    assert df["DI"].iloc[4] == exp["component"]
    assert df["DI"].iloc[1] == "" and df["DI"].iloc[2] == "" and df["DI"].iloc[3] == ""   # dup / ambiguous / preprint-only
    assert df["DI"].iloc[5] == ""                                                           # no match

    assert stats["scanned"] == 6 and stats["filled"] == 2
    assert stats["skipped"] == {"ambiguous": 1, "preprint_only": 1, "duplicate": 1, "type_rejected": 0,
                                "no_match": 1, "api_error": 0, "suspect": 0}
    assert stats["sources_exhausted"] == []
    assert any(k == "jobs.status.doiLookup" and p["total"] == 6 for k, p in ctx.status)

    # provenance CSV: UTF-8 with BOM, one row per scanned record, atomic (no temp leftovers)
    csv_path = stats["_csv"]
    assert csv_path.parent == tmp_path and csv_path.name.startswith("doi_lookup_") and csv_path.suffix == ".csv"
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not list(tmp_path.glob("*.tmp~"))
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == list(enricher._PROVENANCE_COLUMNS)
    by_outcome = {r["outcome"]: r for r in rows if r["outcome"] != "filled"}
    assert len(rows) == 6 and [r["outcome"] for r in rows].count("filled") == 2
    assert by_outcome["duplicate"]["duplicate_of"] == "holder" and by_outcome["duplicate"]["written_doi"] == ""
    assert "do not use as the journal DOI" in by_outcome["preprint_only"]["warning"]
    assert by_outcome["preprint_only"]["preprint_doi"].startswith("10.2139/ssrn")
    assert by_outcome["ambiguous"]["written_doi"] == "" and by_outcome["no_match"]["source"] == ""
    filled = [r for r in rows if r["outcome"] == "filled"]
    assert {r["written_doi"] for r in filled} == {exp["exact"], exp["component"]}
    assert all(r["source"] == "crossref" and r["cand_type"] == "journal-article" for r in filled)


def test_doi_pass_never_assigns_one_doi_to_two_rows(tmp_path, monkeypatch):
    rec, items, truth = case("exact")
    df = pd.DataFrame([{**rec, "UID": "a", "DI": ""}, {**rec, "UID": "b", "DI": ""}])
    _install_crossref_by_title(monkeypatch, {rec["TI"][:25]: items})
    stats = enricher._new_doi_stats()
    asyncio.run(enricher._doi_pass(Ctx(), df, out_dir=tmp_path, stats=stats))
    assert list(df["DI"]) == [truth.lower(), ""]
    assert stats["filled"] == 1 and stats["skipped"]["duplicate"] == 1
    with open(stats["_csv"], encoding="utf-8-sig", newline="") as fh:
        dup = [r for r in csv.DictReader(fh) if r["outcome"] == "duplicate"][0]
    assert dup["duplicate_of"] == "a"


def test_doi_pass_handles_a_float_all_nan_di_column(tmp_path, monkeypatch):
    rec, items, truth = case("exact")
    df = pd.DataFrame([{**rec, "UID": "u0", "DI": float("nan")}])
    assert df["DI"].dtype != object
    _install_crossref_by_title(monkeypatch, {rec["TI"][:25]: items})
    asyncio.run(enricher._doi_pass(Ctx(), df, out_dir=tmp_path))
    assert df["DI"].iloc[0] == truth.lower()


def test_doi_pass_reports_exhausted_sources(tmp_path, monkeypatch):
    rec, _items, _ = case("exact")
    df = pd.DataFrame([{**rec, "UID": f"u{i}", "DI": ""} for i in range(3)])
    n = Net(crossref=[], openalex_resp=Resp({}, 429, {"Retry-After": "86400"}))
    monkeypatch.setattr(A, "_http_get", n)
    stats = enricher._new_doi_stats()
    asyncio.run(enricher._doi_pass(Ctx(), df, out_dir=tmp_path, stats=stats))
    assert stats["sources_exhausted"] == ["openalex"] and stats["skipped"]["no_match"] == 3
    assert n.kinds().count("openalex") == 1


def test_doi_pass_respects_cancellation(tmp_path, monkeypatch):
    df, answers, _ = _rows_for_enricher()
    _install_crossref_by_title(monkeypatch, answers)
    ctx = Ctx()
    ctx.cancelled = True
    stats = enricher._new_doi_stats()
    asyncio.run(enricher._doi_pass(ctx, df, out_dir=tmp_path, stats=stats))
    assert stats["scanned"] == 0 and stats["provenance"] is None


def _prepare_run(tmp_path, monkeypatch):
    """Wire run_fill_all to two temp analyses; the 'active' one flips to A2 during the first lookup."""
    df, answers, exp = _rows_for_enricher()
    start, other = tmp_path / "A1" / "merged.parquet", tmp_path / "A2" / "merged.parquet"
    for p in (start, other):
        p.parent.mkdir()
        df.to_parquet(p, index=False)
    state = {"active": "A1"}

    def handler(params):
        state["active"] = "A2"              # the user switches analysis while the job is running
        q = params.get("query.bibliographic", "")
        for prefix, items in answers.items():
            if q.upper().startswith(prefix.upper()):
                return items
        return []
    monkeypatch.setattr(A, "_http_get", Net(crossref=handler))
    monkeypatch.setattr(enricher, "_active_path", lambda pid: start if state["active"] == "A1" else other)
    monkeypatch.setattr(enricher.analyses, "get_active_analysis_id", lambda pid: state["active"])
    monkeypatch.setattr(enricher, "_snapshot", lambda pid, d, tag, base=None: f"snapshots/{tag}.parquet")
    monkeypatch.setattr(enricher.storage, "touch_project", lambda pid: None)
    exp["titles"] = dict(zip(df["UID"], df["TI"]))
    return start, other, exp


def test_fill_all_writes_back_to_the_start_dataset_even_if_the_active_analysis_changes(tmp_path, monkeypatch):
    start, other, exp = _prepare_run(tmp_path, monkeypatch)
    # the DOI's own metadata returns each record's own title: the second-insurance check passes
    monkeypatch.setattr(A, "extract_metadata", lambda doi, current, **kw: {
        "TI": exp["titles"][current["UID"]], "AB": "abstract of " + doi})
    ctx = Ctx()
    result = asyncio.run(enricher.run_fill_all(ctx, "p1"))

    done, untouched = pd.read_parquet(start), pd.read_parquet(other)
    assert done["DI"].iloc[0] == exp["exact"] and done["DI"].iloc[4] == exp["component"]
    assert (untouched["DI"].iloc[:6] == "").all()             # the now-active analysis was NOT written
    assert result["analysis_id"] == "A1"                       # the analysis the job STARTED on
    assert result["snapshot"] == "snapshots/fill_all.parquet"
    assert result["doi"]["filled"] == 2 and result["doi"]["scanned"] == 6
    assert result["doi"]["provenance"] and result["doi"]["provenance"].endswith(".csv")
    assert set(result["doi"]) == {"scanned", "filled", "skipped", "sources_exhausted", "provenance"}
    assert list(start.parent.glob("doi_lookup_*.csv")) and not list(other.parent.glob("doi_lookup_*.csv"))
    assert done["AB"].iloc[0].startswith("abstract of") and done["AB"].iloc[4].startswith("abstract of")
    assert done["AB"].iloc[1] == ""                            # the abstained records got nothing
    keys = [k for k, _ in ctx.status]
    assert keys[0] == "jobs.status.loading" and "jobs.status.apiPass" in keys
    assert "jobs.status.writing" in keys and keys[-1] == "jobs.status.done"


def test_api_pass_removes_a_doi_whose_title_does_not_match_the_record(tmp_path, monkeypatch):
    start, other, exp = _prepare_run(tmp_path, monkeypatch)

    def fake(doi, current, **kw):               # the component record's new DOI points to a different paper
        if doi.lower() == exp["component"]:
            return {"TI": "A completely different paper about something else", "AB": "WRONG ABSTRACT"}
        return {"TI": exp["titles"][current["UID"]], "AB": "ok"}
    monkeypatch.setattr(A, "extract_metadata", fake)
    result = asyncio.run(enricher.run_fill_all(Ctx(), "p1"))

    done = pd.read_parquet(start)
    assert done["DI"].iloc[0] == exp["exact"]
    assert done["DI"].iloc[4] == ""                            # reverted
    assert done["AB"].iloc[4] == ""                            # nothing imported from the wrong work
    d = result["doi"]
    assert d["filled"] == 1 and d["skipped"]["suspect"] == 1 and d["skipped"]["no_match"] == 2
    with open(next(start.parent.glob("doi_lookup_*.csv")), encoding="utf-8-sig", newline="") as fh:
        rows = {r["UID"]: r for r in csv.DictReader(fh)}
    row = rows[done["UID"].iloc[4]]
    assert row["outcome"] == "suspect_after_fetch" and row["written_doi"] == "" and row["warning"]


def test_same_work_titles_accepts_the_main_title_but_not_a_different_work():
    same = A.same_work_titles
    # Crossref keeps the subtitle in its own field, so the re-check often sees only the main title
    assert same("DEEP LEARNING: METHODS AND APPLICATIONS", "Deep learning")[0]
    assert same("Deep learning", "Deep learning: methods and applications")[0]
    assert same("Deep learning: methods and applications", "Deep learning: methods and applications")[0]
    # not a different work, not a series designator or marker tail, not a single word
    assert not same("Deep learning: methods and applications", "Deep reinforcement learning")[0]
    assert not same("Deep learning. Part II", "Deep learning")[0]
    assert not same("Deep learning: erratum", "Deep learning")[0]
    assert not same("Introduction: foundations of the field", "Introduction")[0]


def test_api_pass_keeps_a_doi_when_the_api_returns_only_the_main_title(monkeypatch):
    df = pd.DataFrame([{"UID": "u1", "TI": "DEEP LEARNING: METHODS AND APPLICATIONS", "DI": "10.1000/dl.1", "AB": ""}])
    monkeypatch.setattr(A, "extract_metadata", lambda doi, current, **kw: {**current, "TI": "Deep learning", "AB": "abstract"})
    verdicts = {}
    asyncio.run(enricher._api_pass(Ctx(), df, fresh={0: df["TI"].iloc[0]}, verdicts=verdicts))
    assert df["DI"].iloc[0] == "10.1000/dl.1" and df["AB"].iloc[0] == "abstract" and verdicts == {}


def test_lookup_cache_is_bounded(net, monkeypatch):
    monkeypatch.setattr(A, "CACHE_MAX_ENTRIES", 3)
    net(crossref=[])
    state = A.DoiLookupState()
    for i in range(6):
        A.lookup_doi({**REC, "TI": f"A DISTINCT TITLE NUMBER {i} ABOUT NOTHING AT ALL"}, state=state)
    assert len(state.cache) <= 3


def test_fill_all_raises_analysis_gone_when_the_analysis_folder_disappears(tmp_path, monkeypatch):
    import shutil
    start, other, exp = _prepare_run(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "extract_metadata", lambda doi, current, **kw: {})
    live = A._http_get

    def deleting(url, params=None, headers=None):
        shutil.rmtree(start.parent, ignore_errors=True)
        return live(url, params, headers)
    monkeypatch.setattr(A, "_http_get", deleting)
    with pytest.raises(RuntimeError, match=r"^analysis_gone"):
        asyncio.run(enricher.run_fill_all(Ctx(), "p1"))
    assert not start.exists() and other.exists()


# ═══ Routes ═════════════════════════════════════════════════════════════════════
def _client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    sys.path.insert(0, str(_API_ROOT))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def test_doi_log_route_serves_the_latest_csv_of_the_active_analysis(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from services import analyses

    pid = client.post("/api/projects", json={"name": "DoiLog"}).json()["id"]
    url = f"/api/projects/{pid}/enrich/doi-log"
    assert client.get(url).status_code == 404                               # no analysis yet
    aid, adir = analyses.create_analysis(pid, "smart")
    analyses.finalize_analysis(pid, aid)
    analyses.set_active_analysis(pid, aid)
    r = client.get(url)
    assert r.status_code == 404 and "doi_log_not_found" in r.text
    (adir / "doi_lookup_20260101_090000.csv").write_text("old", encoding="utf-8")
    (adir / "doi_lookup_20260102_090000.csv").write_text("UID,outcome\nu1,filled\n", encoding="utf-8")
    r = client.get(url)
    assert r.status_code == 200 and r.text.startswith("UID,outcome")
    assert "text/csv" in r.headers["content-type"]
    assert "doi_lookup_20260102_090000.csv" in r.headers["content-disposition"]
    # another analysis becomes active -> its (missing) log is what counts
    aid2, _ = analyses.create_analysis(pid, "smart")
    analyses.finalize_analysis(pid, aid2)
    analyses.set_active_analysis(pid, aid2)
    assert client.get(url).status_code == 404
    assert client.get("/api/projects/nope/enrich/doi-log").status_code == 404


def test_last_fill_report_passes_the_doi_fields_through(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from services import analyses, audit

    pid = client.post("/api/projects", json={"name": "Report"}).json()["id"]
    aid, _ = analyses.create_analysis(pid, "smart")
    analyses.finalize_analysis(pid, aid)
    analyses.set_active_analysis(pid, aid)
    doi = {"scanned": 6, "filled": 2,
           "skipped": {"ambiguous": 1, "preprint_only": 1, "duplicate": 1, "type_rejected": 0,
                       "no_match": 1, "api_error": 0, "suspect": 0},
           "sources_exhausted": ["openalex"], "provenance": "projects/x/analyses/y/doi_lookup_1.csv"}
    audit.write(pid, kind="enrich_api", title="fill", analysis_id=aid,
                details={"method": "fill_all", "analysis_id": aid, "enriched": 3, "doi": doi})
    rep = client.get(f"/api/projects/{pid}/quality/last-fill-report").json()["report"]
    assert rep["doi"] == doi


# ── Review fixes: score-first ranking, uncached failures, suffix-only omission, repositories ──
CH_TITLE = "Deep learning for medical image segmentation in clinical practice"
CH_REC = {"TI": CH_TITLE.upper(), "AU": "SMITH, J", "PY": "2020", "SO": "HANDBOOK OF MEDICAL IMAGING",
          "DT": "Book Chapter", "UID": "c1"}


def test_exact_container_matching_chapter_beats_weaker_journal_article(net):
    # class order must not override a materially better match
    journal = item("10.1/a", CH_TITLE, subtitle="a survey", year=2021, container="Journal of Test",
                   issn=("1111-2222",), authors=(("Smith", "J"),))
    chapter = item("10.2/b", CH_TITLE, type="book-chapter", year=2020, container="Handbook of Medical Imaging",
                   issn=(), authors=(("Smith", "J"),))
    for order in ([journal, chapter], [chapter, journal]):
        d, _ = lookup(CH_REC, order, net)
        assert d.outcome == "filled" and d.doi == "10.2/b", order


def test_equal_evidence_across_classes_abstains(net):
    # two different DOIs within 0.02 at the top -> ambiguous, whatever their classes
    rec = {k: v for k, v in CH_REC.items() if k != "DT"}
    a = item("10.1/a", CH_TITLE, year=2020, container="Handbook of Medical Imaging", issn=(),
             authors=(("Smith", "J"),))
    b = item("10.2/b", CH_TITLE, type="book-chapter", year=2020, container="Handbook of Medical Imaging",
             issn=(), authors=(("Smith", "J"),))
    d, _ = lookup(rec, [a, b], net)
    assert d.outcome == "ambiguous" and d.doi is None


def test_document_type_affinity_is_a_small_bonus_only(net):
    assert A._expected_types("Book Chapter") == {"book-chapter"}
    assert A._expected_types("Proceedings Paper") == {"proceedings-article"}
    assert A._expected_types("Article; Proceedings Paper") == {"journal-article", "proceedings-article"}
    assert A._expected_types("Review") == {"journal-article"}
    assert A._expected_types("") == frozenset()
    # a slightly weaker candidate whose type matches DT wins over a stronger-by-0.01 other type
    a = item("10.1/a", CH_TITLE, year=2020, container="Journal of Test", issn=(), authors=(("Smith", "J"),))
    b = item("10.2/b", CH_TITLE, type="book-chapter", year=2020, container="Journal of Test", issn=(),
             authors=(("Smith", "J"),))
    ra = A._build_rec({**CH_REC, "DT": "Article"})
    rb = A._build_rec({**CH_REC, "DT": "Book Chapter"})
    ea = A._evaluate(ra, A._candidate_from_crossref(a, 0))
    eb = A._evaluate(ra, A._candidate_from_crossref(b, 1))
    assert A._rank_score(ea, ra) > A._rank_score(eb, ra)
    assert A._rank_score(eb, rb) > A._rank_score(ea, rb)
    assert A.TYPE_AFFINITY_BONUS < A.SCORE_MARGIN


def test_metadata_extractors_do_not_cache_failures(monkeypatch):
    for fn, url_part in ((A.extract_metadata_from_crossref, "crossref"), (A.extract_metadata_from_openalex, "openalex")):
        fn.cache_clear()
        calls = []
        ok_work = ({"message": {"title": ["T"], "container-title": ["J"]}} if url_part == "crossref"
                   else {"title": "T", "display_name": "T", "primary_location": {"source": {"display_name": "J"}}})

        def fake(url, params=None, headers=None, attempts=3, _calls=calls, _ok=ok_work, **kw):
            _calls.append(url)
            return Resp({}, 429, {"Retry-After": "30"}) if len(_calls) == 1 else Resp(_ok)

        monkeypatch.setattr(A, "_get_with_retry", fake)
        assert fn("10.1/x") == {}                       # transient failure
        second = fn("10.1/x")                           # healthy now: must be fetched again
        assert second and len(calls) == 2
        assert fn("10.1/x") == second and len(calls) == 2      # a success IS cached
        fn.cache_clear()


@pytest.mark.parametrize("variant", [
    "Re: {t}", "Rebuttal: {t}", "{t}: Rebuttal", "{t}: Re", "Closure: {t}", "Discussion: {t}", "Update: {t}",
    "Notice of Duplicate Publication: {t}", "Slides: {t}", "Poster: {t}", "Presentation: {t}",
    "Data from: {t}", "Reprint: {t}", "Fe de erratas: {t}", "Berichtigung: {t}", "Rectificatif: {t}",
    "{t}: Supplementary Information", "Abstract of {t}", "A new twist on the topic: {t}",
])
def test_prefix_and_marker_variants_never_match(net, variant):
    t = "Assessing large language models for clinical trial recruitment in hospitals"
    rec = {**REC, "TI": t.upper()}
    cand = item("10.1000/var.1", variant.format(t=t), authors=(("Smith", "John"), ("Doe", "Alice")))
    d, _ = lookup(rec, [cand], net)
    assert d.doi is None, variant
    assert not A.titles_match(t, variant.format(t=t))[0], variant
    assert not A.same_work_titles(t, variant.format(t=t))[0], variant


@pytest.mark.parametrize("variant", [
    "{t} [Retracted]", "{t} [Video]", "{t} [Erratum]", "{t}: Video", "{t}: Appendix", "{t}: Graphical Abstract",
])
def test_bracketed_or_suffixed_marker_is_not_stripped_away(variant):
    t = "Assessing large language models for clinical trial recruitment in hospitals"
    c = variant.format(t=t)
    assert not A.titles_match(t, c)[0], c
    assert not A.titles_match(c, t)[0], c
    assert not A.same_work_titles(t, c)[0], c


def test_rival_exactly_at_the_margin_is_still_a_rival(monkeypatch):
    # float noise must not decide whether a 0.02 gap abstains: sweep a grid of exact-margin gaps
    for i in range(8000, 11000):
        top = i / 10000
        low = round(top - A.SCORE_MARGIN, 4)
        e1 = A._Eval(cand=A.Candidate(doi="10.1/a", type="journal-article"), ok=True, score=top)
        e2 = A._Eval(cand=A.Candidate(doi="10.1/b", type="journal-article", rank=1), ok=True, score=low)
        monkeypatch.setattr(A, "_evaluate", lambda r, c, _m={"10.1/a": e1, "10.1/b": e2}: _m[c.doi])
        r = type("R", (), {"preprint": False, "expected_types": frozenset(), "title": "x"})()
        d = A._decide(r, [e1.cand, e2.cand], A.DoiLookupState(), set(), None, False)
        assert d.outcome == "ambiguous", (top, low)


def test_suffix_subtitle_is_still_tolerated_both_ways(net):
    t = "Assessing large language models for clinical trial recruitment in hospitals"
    rec = {**REC, "TI": t.upper()}
    cand = item("10.1000/sfx.1", t, subtitle="a multicentre evaluation", authors=(("Smith", "John"), ("Doe", "Alice")))
    d, _ = lookup(rec, [cand], net)
    assert d.outcome == "filled" and d.doi == "10.1000/sfx.1"
    rec2 = {**REC, "TI": (t + ": a multicentre evaluation").upper()}
    cand2 = item("10.1000/sfx.2", t, authors=(("Smith", "John"), ("Doe", "Alice")))
    d2, _ = lookup(rec2, [cand2], net)
    assert d2.outcome == "filled" and d2.doi == "10.1000/sfx.2"


def test_bracketed_translation_form_matches_but_keeps_markers(net):
    t = "Strategies and guidelines for the aesthetic design of codes in advertising"
    es = "Estrategias y directrices para la configuracion estetica del codigo en publicidad"
    cand = item("10.1000/tr.1", es, authors=(("Smith", "John"),))
    d, _ = lookup({**REC, "TI": f"{t.upper()}; [{es.upper()}]"}, [cand], net)
    assert d.doi == "10.1000/tr.1"
    d2, _ = lookup({**REC, "TI": f"ERRATUM TO: {t.upper()}; [{es.upper()}]"}, [cand], net)
    assert d2.doi is None


@pytest.mark.parametrize("doi,container", [
    ("10.5281/zenodo.123456", "Zenodo"), ("10.6084/m9.figshare.99999", "figshare"),
    ("10.17605/osf.io/abcde", "OSF"), ("10.7910/dvn/abcdef", "Harvard Dataverse"),
    ("10.5061/dryad.abc123", "Dryad"), ("10.17632/abc123.1", "Mendeley Data"),
])
def test_repository_dois_are_never_written_as_the_paper_doi(net, doi, container):
    # Crossref has nothing; the OpenAlex fallback returns a self-archived repository copy
    work = {"doi": f"https://doi.org/{doi}", "title": REC["TI"].title(), "publication_year": 2024, "type": "article",
            "primary_location": {"source": {"display_name": container}},
            "authorships": [{"author": {"display_name": "John Smith"}}]}
    n = net(crossref=[], openalex=[work])
    d = A.lookup_doi(REC, state=A.DoiLookupState())
    assert d.outcome == "preprint_only" and d.doi is None
    assert d.preprint_doi == doi
    assert d.warning == "repository copy DOI - do not use as the publisher DOI"
    # the same through Crossref, even for a record that is itself a preprint
    cr = item(doi, REC["TI"].title(), container=container, issn=(), authors=(("Smith", "John"),))
    for rec in (REC, {**REC, "SO": "ARXIV", "SN": ""}):
        n = net(crossref=[cr])
        d2 = A.lookup_doi(rec, state=A.DoiLookupState())
        assert d2.outcome == "preprint_only" and d2.doi is None and "repository" in d2.warning, rec["SO"]


def test_published_journal_wins_over_a_repository_copy(net):
    z = item("10.5281/zenodo.1", REC["TI"].title(), container="Zenodo", issn=(), authors=(("Smith", "John"),))
    d, _ = lookup(REC, [z, CAND], net)
    assert d.outcome == "filled" and d.doi == "10.1000/test.1"
