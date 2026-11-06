"""Affiliation candidate builder (services.disambiguation.affiliation_graph).

Small synthetic datasets (UID 1..N + C1) pin the properties of the field-aware graph:
spelling variants become a tier-1 proposal, different institutions never share a tier-1
candidate, one LLM request never exceeds the cap and the output is deterministic.
"""

import random
import string
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from services.disambiguation import affiliation_graph, blocking  # noqa: E402


def _df(addresses, **extra):
    n = len(addresses)
    cols = {"UID": list(range(1, n + 1)), "C1": list(addresses)}
    cols.update(extra)
    return pd.DataFrame(cols)


def _build(addresses, **extra):
    return blocking.build_affiliation_blocks(_df(addresses, **extra))


def _tier1(blocks):
    return [c for b in blocks for c in b.get("auto", [])]


def _tier2(blocks):
    return [c for b in blocks for c in b.get("members", [])]


def _variants_of(blocks):
    return {v for c in _tier1(blocks) + _tier2(blocks) for v in c["variants"]}


def _same_block(blocks, a, b):
    """True when both addresses are variants in the members of one block (one LLM request)."""
    for blk in blocks:
        vs = {v for c in blk.get("members", []) for v in c["variants"]}
        if a in vs and b in vs:
            return True
    return False


def _tier1_with(blocks, address):
    return [c for c in _tier1(blocks) if address in c["variants"]]


def test_spelling_variants_are_one_tier1_candidate_longest_first():
    short = "UNIV PASSAU, PASSAU, GERMANY"
    full = "UNIVERSITY OF PASSAU, PASSAU, GERMANY"
    blocks = _build([short, full, "TECH UNIV MUNICH, MUNICH, GERMANY"])
    cands = _tier1_with(blocks, short)
    assert len(cands) == 1
    assert cands[0]["tier"] == 1
    assert cands[0]["variants"] == [full, short]
    assert cands[0]["records"] == ["1", "2"]


def test_different_universities_never_share_a_tier1_candidate():
    a = "GEORGETOWN UNIVERSITY, WASHINGTON, UNITED STATES"
    b = "UNIVERSITY OF GEORGIA, ATHENS, UNITED STATES"
    a2 = "GEORGETOWN UNIV, WASHINGTON, UNITED STATES"
    blocks = _build([a, b, a2, "UNIV GEORGIA, ATHENS, UNITED STATES"])
    for c in _tier1(blocks):
        assert not (any("GEORGETOWN" in v for v in c["variants"]) and any("GEORGIA" in v for v in c["variants"]))
    assert not any(a in c["variants"] and b in c["variants"] for c in _tier1(blocks))


def test_university_of_california_campuses_are_not_one_tier1_candidate():
    rows = [
        "UNIV CALIF BERKELEY, BERKELEY, CA, UNITED STATES",
        "UNIVERSITY OF CALIFORNIA BERKELEY, BERKELEY, UNITED STATES",
        "UNIV CALIF LOS ANGELES, LOS ANGELES, CA, UNITED STATES",
        "UNIVERSITY OF CALIFORNIA LOS ANGELES, LOS ANGELES, UNITED STATES",
    ]
    blocks = _build(rows)
    for c in _tier1(blocks):
        has_b = any("BERKELEY" in v for v in c["variants"])
        has_l = any("LOS ANGELES" in v for v in c["variants"])
        assert not (has_b and has_l), c["variants"]


def test_acronym_and_spelled_out_name_share_a_request():
    ucl = "UCL, LONDON, ENGLAND"
    full = "UNIVERSITY COLLEGE LONDON, LONDON, UNITED KINGDOM"
    blocks = _build([ucl, full, "UNIVERSITY OF OXFORD, OXFORD, ENGLAND"])
    assert _same_block(blocks, ucl, full)


def test_chaining_stress_keeps_requests_small_and_institutions_apart():
    """300 institutions x 4 spellings that share structure words: the graph really produces
    LLM requests (the previous dataset produced none, so the asserts checked nothing)."""
    rng = random.Random(4)
    names = set()
    while len(names) < 300:
        names.add("".join(rng.choice(string.ascii_lowercase) for _ in range(8)).upper())
    rows = []
    for nm in sorted(names):
        rows.append(f"UNIV {nm}, DEPT PHYS, CITY{nm}, GERMANY")
        rows.append(f"UNIVERSITY OF {nm}, CITY{nm}, GERMANY")
        rows.append(f"{nm} UNIVERSITY OF TECHNOLOGY, CITY{nm}, GERMANY")
        rows.append(f"DEPARTMENT OF HISTORY, {nm} UNIVERSITY, CITY{nm}, GERMANY")
    blocks = _build(rows)
    requests = [b for b in blocks if b.get("members")]
    assert len(requests) >= 20, len(requests)
    assert sum(len(c["variants"]) for b in requests for c in b["members"]) == len(rows)
    for blk in blocks:
        total = sum(len(c["variants"]) for c in blk.get("auto", []) + blk.get("members", []))
        assert total <= 40, (blk["key"], total)
    for c in _tier1(blocks) + _tier2(blocks):
        unis = {nm for nm in names if any(nm in v for v in c["variants"])}
        assert len(unis) == 1, c["variants"]


def test_cap_is_40_on_an_over_cap_family():
    rng = random.Random(3)
    words = sorted({"".join(rng.choice(string.ascii_lowercase) for _ in range(9)) for _ in range(400)})
    rows = [f"DEPARTMENT OF {w.upper()} STUDIES, UNIVERSITY OF PASSAU, PASSAU, GERMANY" for w in words[:120]]
    blocks = _build(rows)
    sizes = [sum(len(c["variants"]) for c in b.get("members", [])) for b in blocks]
    assert sizes and max(sizes) <= 40, sizes          # literal, not the module constant
    assert sum(sizes) == 120                           # every variant is sent exactly once
    assert len(blocks) >= 3


def test_same_name_in_two_cities_is_not_tier1():
    a = "WESTMINSTER COLL, FULTON, MO, USA"
    b = "WESTMINSTER COLLEGE, NEW WILMINGTON, PA, USA"
    blocks = _build([a, b])
    assert not any(a in c["variants"] and b in c["variants"] for c in _tier1(blocks))
    assert _same_block(blocks, a, b)


def test_acronym_links_without_shared_city():
    mit = "MIT, CAMBRIDGE, MA, USA"
    full = "MASSACHUSETTS INSTITUTE OF TECHNOLOGY, BOSTON, MA, USA"
    assert _same_block(_build([mit, full]), mit, full)


def test_spelling_group_with_linked_neighbour_goes_to_llm_not_tier1():
    rows = ["UNIV PASSAU, PASSAU, GERMANY", "UNIVERSITY OF PASSAU, PASSAU, GERMANY",
            "PASSAU UNIV, DEPT PHYS, PASSAU, GERMANY"]
    blocks = _build(rows)
    assert _tier1(blocks) == []
    assert _same_block(blocks, rows[0], rows[2]) and _same_block(blocks, rows[1], rows[2])
    cand = [c for b in blocks for c in b.get("members", []) if rows[0] in c["variants"]]
    assert len(cand) == 1 and rows[1] in cand[0]["variants"]


def test_univ_georgia_and_georgetown_in_one_city_are_not_tier1():
    a, b = "UNIV GEORGIA, ATHENS, GA, USA", "GEORGETOWN UNIV, ATHENS, GA, USA"
    assert not any(a in c["variants"] and b in c["variants"] for c in _tier1(_build([a, b])))


def test_records_of_a_candidate_are_capped_at_100():
    short, full = "UNIV PASSAU, PASSAU, GERMANY", "UNIVERSITY OF PASSAU, PASSAU, GERMANY"
    blocks = _build([short] * 150 + [full] * 150)
    cands = _tier1_with(blocks, short)
    assert len(cands) == 1
    assert len(cands[0]["records"]) == 100


# ---- regressions: saint cities, institution numbers, non-Latin names

def _together_in_tier1(blocks, a, b):
    return any(a in c["variants"] and b in c["variants"] for c in _tier1(blocks))


def test_st_thomas_in_two_cities_is_not_tier1():
    a = "UNIV ST THOMAS, ST PAUL, MN, USA"
    b = "UNIVERSITY OF ST THOMAS, HOUSTON, TX, USA"
    blocks = _build([a, b])
    assert not _together_in_tier1(blocks, a, b)
    assert _same_block(blocks, a, b)          # still sent to the LLM


def test_leading_st_is_a_saint_city_not_a_street():
    geo = affiliation_graph._is_geo
    for city in ("ST PAUL", "ST LOUIS", "ST ANDREWS", "ST JOHNS", "SAINT PAUL"):
        assert not geo(city, "United States"), city
    for street in ("ST GEORGES RD", "1 ST PAULS SQ", "123 ST PAUL ST", "ST PAULS SQ", "ST"):
        assert geo(street, "United States"), street
    parsed = affiliation_graph._Addr("UNIV ST THOMAS, ST PAUL, MN, USA")
    assert parsed.place == {"st", "paul"}


def test_numbered_institutions_are_not_one_tier1_candidate():
    pairs = [
        ("UNIV PARIS 05, PARIS, FRANCE", "UNIV PARIS 07, PARIS, FRANCE"),
        ("Univ Lyon 1, Lyon, France", "Univ Lyon 2, Lyon, France"),
        ("Univ Rennes 1, F-35000 Rennes, France", "Univ Rennes 2, F-35000 Rennes, France"),
        ("City Hosp 3, Moscow, Russia", "City Hosp 5, Moscow, Russia"),
    ]
    for a, b in pairs:
        assert not _together_in_tier1(_build([a, b]), a, b), (a, b)
    # all of them in one dataset
    flat = [x for p in pairs for x in p]
    blocks = _build(flat)
    for a, b in pairs:
        assert not _together_in_tier1(blocks, a, b), (a, b)


def test_same_institution_number_written_differently_is_still_tier1():
    a, b = "UNIV PARIS 05, PARIS, FRANCE", "UNIVERSITY OF PARIS 5, PARIS, FRANCE"
    assert _together_in_tier1(_build([a, b]), a, b)


def test_postal_code_only_differences_stay_tier1():
    brite = ["BRITE PROFESSIONAL SERVICES, SYDNEY, 2152, NSW, AUSTRALIA",
             "BRITE PROFESSIONAL SERVICES, SYDNEY, 2751, NSW, AUSTRALIA"]
    assert _together_in_tier1(_build(brite), *brite)
    purdue = ["Purdue Univ, Dept Comp Sci, W Lafayette, IN 47907 USA",
              "Purdue Univ, Dept Comp Sci, W Lafayette, IN 47906 USA"]
    assert _together_in_tier1(_build(purdue), *purdue)


def test_non_latin_institution_names_are_never_tier1():
    for a, b in [("東京大学, 7-3-1 Hongo, Tokyo, Japan", "早稲田大学, Tokyo, Japan"),
                 ("北京大学, Beijing, China", "清华大学, Beijing, Peoples R China")]:
        blocks = _build([a, b])
        assert not _together_in_tier1(blocks, a, b), (a, b)
        assert _tier1_with(blocks, a) == [] and _tier1_with(blocks, b) == []
        assert affiliation_graph._Addr(a).lost


def test_candidates_never_mix_two_known_countries():
    rows = [
        "UNIVERSITY OF LIMA, LIMA, PERU",
        "UNIV LIMA, LIMA, PERU",
        "UNIVERSITY OF LIMA, LIMA, OHIO, UNITED STATES",
        "UNIVERSITY OF CAMBRIDGE, CAMBRIDGE, ENGLAND",
        "UNIVERSITY OF CAMBRIDGE, CAMBRIDGE, MA, UNITED STATES",
        "UNIV CAMBRIDGE, CAMBRIDGE, ENGLAND",
        "UNIVERSITY OF MANCHESTER, MANCHESTER, ENGLAND",
    ]
    blocks = _build(rows)
    for c in _tier1(blocks) + _tier2(blocks):
        countries = {affiliation_graph._Addr(v).country for v in c["variants"]}
        assert len(countries) == 1, c["variants"]


def test_block_keys_unique_and_records_are_uid_strings():
    rows = [
        "UNIV PASSAU, PASSAU, GERMANY", "UNIVERSITY OF PASSAU, PASSAU, GERMANY",
        "UCL, LONDON, ENGLAND", "UNIVERSITY COLLEGE LONDON, LONDON, UNITED KINGDOM",
        "UNIV CALIF BERKELEY, BERKELEY, UNITED STATES", "UNIVERSITY OF CALIFORNIA BERKELEY, UNITED STATES",
        "MIT, CAMBRIDGE, UNITED STATES", "MASSACHUSETTS INSTITUTE OF TECHNOLOGY, CAMBRIDGE, UNITED STATES",
    ]
    blocks = _build(rows)
    assert blocks
    keys = [b["key"] for b in blocks]
    assert len(keys) == len(set(keys))
    ids = [c["id"] for c in _tier1(blocks) + _tier2(blocks)]
    assert len(ids) == len(set(ids))
    for c in _tier1(blocks) + _tier2(blocks):
        assert c["records"] and all(isinstance(r, str) and r.isdigit() for r in c["records"])
        assert all(1 <= int(r) <= len(rows) for r in c["records"])


def test_output_is_identical_on_two_runs():
    rng = random.Random(8)
    base = ["UNIV PASSAU, PASSAU, GERMANY", "UNIVERSITY OF PASSAU, PASSAU, GERMANY",
            "UNIVERSITY OF OXFORD, OXFORD, ENGLAND", "OXFORD UNIV, OXFORD, ENGLAND",
            "UCL, LONDON, ENGLAND", "UNIVERSITY COLLEGE LONDON, LONDON, ENGLAND"]
    rows = [rng.choice(base) for _ in range(60)]
    assert _build(rows) == _build(rows)


def test_missing_c1_is_skipped_and_falls_back_to_c1raw():
    c1 = [None, np.nan, "", "UNIV PASSAU, PASSAU, GERMANY", pd.NA, "UNIVERSITY OF PASSAU, PASSAU, GERMANY"]
    raw = ["UNIVERSITY OF PASSAU, PASSAU, GERMANY", "UNIV PASSAU, PASSAU, GERMANY", "UNIV PASSAU, PASSAU, GERMANY",
           None, "UNIVERSITY OF PASSAU, PASSAU, GERMANY", None]
    df = pd.DataFrame({"UID": list(range(1, 7)), "C1": pd.Series(c1, dtype=object),
                       "C1raw": pd.Series(raw, dtype=object)})
    blocks = blocking.build_affiliation_blocks(df)
    variants = _variants_of(blocks)
    assert variants == {"UNIV PASSAU, PASSAU, GERMANY", "UNIVERSITY OF PASSAU, PASSAU, GERMANY"}
    assert not any(v.lower() == "nan" for v in variants)
    cands = _tier1(blocks)
    assert len(cands) == 1
    assert cands[0]["records"] == ["1", "2", "3", "4", "5", "6"]


def test_missing_c1_without_c1raw_column_is_skipped():
    blocks = _build([None, np.nan, "", "UNIV PASSAU, PASSAU, GERMANY", "UNIVERSITY OF PASSAU, PASSAU, GERMANY"])
    assert _variants_of(blocks) == {"UNIV PASSAU, PASSAU, GERMANY", "UNIVERSITY OF PASSAU, PASSAU, GERMANY"}


def test_spanish_de_and_la_are_not_us_state_codes():
    rows = ["UNIVERSIDAD DE LIMA, LIMA, PERU", "UNIV LIMA, LIMA, PERU"]
    blocks = _build(rows)
    cands = _tier1_with(blocks, rows[0])
    assert len(cands) == 1 and set(cands[0]["variants"]) == set(rows)
    parsed = affiliation_graph._Addr(rows[0])
    assert parsed.country == "Peru"
    assert "lima" in parsed.inst


def test_no_c1_column_returns_empty():
    assert blocking.build_affiliation_blocks(pd.DataFrame({"UID": [1, 2], "TI": ["a", "b"]})) == []


def test_cancel_returns_empty_list():
    df = _df(["UNIV PASSAU, PASSAU, GERMANY", "UNIVERSITY OF PASSAU, PASSAU, GERMANY"])
    assert blocking.build_affiliation_blocks(df, should_cancel=lambda: True) == []
