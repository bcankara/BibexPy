"""C1 replace helpers must not touch cells they do not change.

_split_keep used to drop a trailing ';', so replace_affiliation_in_c1 rewrote every C1 cell
that ended with ';' even when the cell did not contain the variant (one approved variant
that occurs once was reported as 79 replacements).
"""

import sys
from pathlib import Path

import pandas as pd

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))
sys.path.insert(0, str(_API_ROOT.parents[1] / "packages"))   # the repo copy, not an installed one

from bibex_core.modules import c1_utils  # noqa: E402
from services.disambiguation import pipeline  # noqa: E402

CELL_TRAILING = "[Smith J] NATL TAIWAN UNIV, TAIPEI, TAIWAN.; [Doe A] UNIV BELGRADE, BELGRADE 11000, SERBIA.;"


def test_split_keep_round_trip_is_lossless():
    for value in ["a;b;", "a;b", ";", ";;", "a;;b;", "[A; B] x, y; z;", " a ; b ;", "x"]:
        assert ";".join(c1_utils._split_keep(value)) == value, value


def test_unmatched_cell_with_trailing_semicolon_stays_byte_identical():
    mapping = {"UNIV OTHER, PARIS, FRANCE": "UNIVERSITY OF OTHER, PARIS, FRANCE"}
    assert c1_utils.replace_affiliation_in_c1(CELL_TRAILING, mapping) == CELL_TRAILING
    assert c1_utils.replace_org_in_c1(CELL_TRAILING, mapping) == CELL_TRAILING
    assert c1_utils.replace_country_in_c1(CELL_TRAILING, {"nowhere": "Somewhere"}) == CELL_TRAILING
    assert c1_utils.append_country_to_c1(CELL_TRAILING, {"nowhere": "Somewhere"}) == CELL_TRAILING


def test_matched_cell_keeps_its_trailing_semicolon():
    mapping = {"UNIV BELGRADE, BELGRADE 11000, SERBIA.": "UNIVERSITY OF BELGRADE, BELGRADE, SERBIA"}
    out = c1_utils.replace_affiliation_in_c1(CELL_TRAILING, mapping)
    assert out.endswith(";")
    assert "UNIVERSITY OF BELGRADE, BELGRADE, SERBIA" in out
    assert "NATL TAIWAN UNIV, TAIPEI, TAIWAN." in out
    assert out.count(";") == CELL_TRAILING.count(";")


def test_apply_counts_only_cells_that_changed():
    cells = [CELL_TRAILING,
             "[X Y] UNIV BELGRADE, BELGRADE 11000, SERBIA.;",
             "[Z] OTHER INST, ROME, ITALY.;",
             "[Z] OTHER INST, ROME, ITALY.",
             "UNIV OTHER, PARIS, FRANCE;"]
    df = pd.DataFrame({"C1": cells})
    mapping = {"UNIV OTHER, PARIS, FRANCE": "UNIVERSITY OF OTHER, PARIS, FRANCE"}
    affected = pipeline._apply_c1_map(df, ["C1"], lambda s: c1_utils.replace_affiliation_in_c1(s, mapping))
    assert affected == 1
    assert df["C1"].tolist()[:4] == cells[:4]
    assert df["C1"].iloc[4] == "UNIVERSITY OF OTHER, PARIS, FRANCE;"
