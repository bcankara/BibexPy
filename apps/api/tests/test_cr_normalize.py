"""CR (cited references) normalization — Scopus grammar rewritten as WoS grammar.

VOSviewer and every other consumer of a WoS plain-text file parse CR with the
WoS reference grammar (``AUTHOR, YYYY, SOURCE, Vnn, Pnn, DOI xx``).  Scopus
exports a different grammar in which ``;`` separates *authors* inside a
reference, so writing Scopus CR straight into a WoS file yields references no
tool can key on.  The unit cases below use real references sampled from a
12,487-record corpus; the integration case proves the export boundary applies
the conversion.
"""

import re
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

# bibex_core lives in <repo>/packages and is put on the path by the dev launcher
# (PYTHONPATH=<repo>\packages).  Mirror that here so the tests exercise the repo
# copy rather than any stale wheel installed into site-packages.
_PACKAGES = _API_ROOT.parents[1] / "packages"
if str(_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_PACKAGES))
    for _mod in [m for m in sys.modules if m == "bibex_core" or m.startswith("bibex_core.")]:
        sys.modules.pop(_mod, None)

from bibex_core.cr_normalize import count_refs, normalize_cr  # noqa: E402


# ── Gerçek örnekler (12.487 kayıtlık korpustan) ──────────────────────────

# Scopus "new format": yazarlar ';' ile, referans sonunda "(yyyy)".
REAL_NEW_FORMAT = [
    (
        "FAHLE L.; HOLLEY E.A.; WALTON G.; PETRUSKA A.J.; BRUNE J.F., ANALYSIS OF "
        "SLAM-BASED LIDAR DATA QUALITY METRICS FOR GEOTECHNICAL UNDERGROUND "
        "MONITORING, MIN METALL EXPLOR, 39, 5, PP. 1939-1960, (2022)",
        "FAHLE L, 2022, MIN METALL EXPLOR, V39, P1939",
    ),
    (
        "USAMENTIAGA R.; MOLLEDA J.; GARCIA D.F., FAST AND ROBUST LASER STRIPE "
        "EXTRACTION FOR 3D RECONSTRUCTION IN INDUSTRIAL ENVIRONMENTS, "
        "MACH. VIS. APPL, 23, 1, PP. 179-196, (2012)",
        "USAMENTIAGA R, 2012, MACH. VIS. APPL, V23, P179",
    ),
    (
        # Cilt var, sayfa yok
        "HULL J.; EWART I.J., AUTOMATION IN CONSTRUCTION CONSERVATION DATA "
        "PARAMETERS FOR BIM-ENABLED HERITAGE ASSET MANAGEMENT, AUTOM CONSTR, "
        "119, (2020)",
        "HULL J, 2020, AUTOM CONSTR, V119",
    ),
    (
        # Cilt + sayı, sayfa yok
        "PIVAC D., AVAILABILITY OF HISTORICAL CADASTRAL DATA, LAND, 10, 9, (2021)",
        "PIVAC D, 2021, LAND, V10",
    ),
    (
        "JING L.; HUDSON J.A., NUMERICAL METHODS IN ROCK MECHANICS, "
        "INT J ROCK MECH MIN SCI, 39, PP. 409-427, (2002)",
        "JING L, 2002, INT J ROCK MECH MIN SCI, V39, P409",
    ),
    (
        # Kitap: yalnız yazar + başlık + yıl. Başlık kaynak sanılmamalı.
        "ALSTON B.; HARRELL J.; SHAW I., ANCIENT EGYPTIAN MATERIALS AND "
        "TECHNOLOGY, (2000)",
        "ALSTON B, 2000",
    ),
    (
        # Tek yazar, yalnız yıl
        "LOUCKS L.J., (1981)",
        "LOUCKS LJ, 1981",
    ),
    (
        # Cilt "730-732" bir aralık — cilt de kaynak da sayılmamalı
        "SAAVEDRA E.; LOPEZ A.J.; LAMAS J.; FIORUCCI M.P.; RAMIL A.; RIVAS T., "
        "FINITE ELEMENT MODEL OF GRANITE ABLATION WITH UV LASER, "
        "MAT. SCIENCE FORUM, 730-732, PP. 519-524, (2013)",
        "SAAVEDRA E, 2013, MAT. SCIENCE FORUM, P519",
    ),
    (
        # Kısaltılmış tireli ilk adlar → WoS "AC" yazımı; cilt yok, sayfa var
        "HAUGSTVEDT A.-C.; KROGSTIE J., MOBILE AUGMENTED REALITY FOR CULTURAL "
        "HERITAGE: A TECHNOLOGY ACCEPTANCE STUDY, ISMAR 2012, PP. 247-255, (2012)",
        "HAUGSTVEDT AC, 2012, ISMAR 2012, P247",
    ),
    (
        # Yazar yok, kurum adı — yine de korunur
        "MINISTER OF CULTURE AND TOURISM, (2005)",
        "MINISTER OF CULTURE AND TOURISM, 2005",
    ),
]

# Gerçek WoS CR hücreleri (aynı korpustan) — bunlar hiç değişmemeli.
REAL_WOS_CELLS = [
    "MILDENHALL B, 2022, COMMUN ACM, V65, P99, DOI 10.1145/3503250;"
    "PASZKE A, 2019, ADV NEUR IN, V32;"
    "TANCIK M., 2020, NEURIPS;"
    "WANG Z., 2021, ARXIV",
    # ASCE DOI'si "(1234)" ile biter — Scopus'un "(yyyy)" işaretiyle karıştırılmamalı
    "SU YY, 2006, J CONSTR ENG M, V132, P1234, "
    "DOI 10.1061/(ASCE)0733-9364(2006)132:12(1234);"
    "KONTOGIANNI V, 2004, J GEOTECH GEOENVIRON, V130, P1004, "
    "DOI 10.1061/(ASCE)1090-0241(2004)130:10(1004)",
    # Yılsız girdiler ve yazarsız WoS referansı
    "AGISOFT, AGISOFT METASHAPE USER MANUAL;"
    "2015, PHOTOGRAMM ENG REM S, V81, P1, DOI 10.14358/PERS.81.3.A1-A26;"
    "[ANONYMOUS], 2002, HAWAIIS RUSSIAN ADV",
]


# ── Birim: Scopus "new format" ───────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", REAL_NEW_FORMAT)
def test_new_format_real_samples(raw, expected):
    assert normalize_cr(raw) == expected


def test_new_format_multi_author_semicolons_are_not_reference_boundaries():
    """Bir referansın içindeki ';' yazar ayırıcıdır — referans bölünmemeli."""
    raw = ("Anderson E.W.; Fornell C.; Lehmann D.R., Customer Satisfaction, "
           "Market Share, And Profitability, J Marketing, 58, 3, pp. 53-66, (1994)")
    assert normalize_cr(raw) == "Anderson EW, 1994, J MARKETING, V58, P53"
    assert count_refs(normalize_cr(raw)) == 1


def test_single_page_and_en_dash_range():
    single = "Smith J.; Doe A., A Title, J Test, 7, pp. 5, (2001)"
    assert normalize_cr(single) == "Smith J, 2001, J TEST, V7, P5"
    # en dash (U+2013) sayfa aralığı
    dashed = "Smith J.; Doe A., A Title, J Test, 7, pp. 12–34, (2001)"
    assert normalize_cr(dashed) == "Smith J, 2001, J TEST, V7, P12"


def test_doi_is_carried_over_when_present():
    raw = ("Aria M.; Cuccurullo C., A Tool, J Informetrics, 11, 4, "
           "pp. 959-975, 10.1016/j.joi.2017.08.007, (2017)")
    out = normalize_cr(raw)
    assert out.endswith("DOI 10.1016/j.joi.2017.08.007")
    assert out.startswith("Aria M, 2017, J INFORMETRICS, V11, P959")


# ── Birim: Scopus "classic format" (yıl metin ortasında) ─────────────────

def test_classic_format():
    raw = ("Aria M., Cuccurullo C., bibliometrix: An R-tool for comprehensive "
           "science mapping analysis, (2017) Journal of Informetrics, 11, 4, "
           "pp. 959-975")
    assert normalize_cr(raw) == "Aria M, 2017, JOURNAL OF INFORMETRICS, V11, P959"


def test_classic_format_semicolon_separates_references():
    """Classic biçimde yazarlar virgülle ayrıldığı için ';' referans sınırıdır."""
    raw = ("Aria M., A Tool, (2017) J Informetrics, 11, pp. 959-975; "
           "Cobo M., Science Mapping, (2011) J Am Soc Inf Sci, 62, pp. 1382-1402")
    out = normalize_cr(raw)
    assert out == ("Aria M, 2017, J INFORMETRICS, V11, P959; "
                   "Cobo M, 2011, J AM SOC INF SCI, V62, P1382")
    assert count_refs(out) == 2


# ── Birim: WoS girdisi aynen geri döner ──────────────────────────────────

@pytest.mark.parametrize("cell", REAL_WOS_CELLS)
def test_wos_cells_pass_through_byte_identical(cell):
    assert normalize_cr(cell) is not None
    assert normalize_cr(cell) == cell


# ── Birim: karışık hücre ─────────────────────────────────────────────────

def test_mixed_format_cell():
    """Tek bir hücre hem WoS hem Scopus referansı taşıyabilir (gerçek veride
    85 satır böyle) — her referans kendi dilbilgisine göre işlenmeli."""
    cell = ("MILDENHALL B, 2022, COMMUN ACM, V65, P99, DOI 10.1145/3503250;"
            "PIVAC D., AVAILABILITY OF HISTORICAL CADASTRAL DATA, LAND, 10, 9, (2021); "
            "ALSTON B.; HARRELL J.; SHAW I., ANCIENT EGYPTIAN MATERIALS, (2000)")
    out = normalize_cr(cell)
    assert out == ("MILDENHALL B, 2022, COMMUN ACM, V65, P99, DOI 10.1145/3503250; "
                   "PIVAC D, 2021, LAND, V10; "
                   "ALSTON B, 2000")
    assert count_refs(out) == 3


# ── Birim: dönüştürülemeyen referanslar ──────────────────────────────────

def test_year_less_reference_is_preserved_with_neutralized_semicolons():
    """Yıl yoksa dönüştürme yapılamaz; referans ATILMAZ, yalnızca içindeki ';'
    virgüle çevrilir — böylece WoS yazıcısının ';' bölmesi referansı parçalamaz
    ve VOSviewer ham dizeyle eşleştirme yapabilir."""
    raw = ("RUBANO V.; VITALI F., MAKING ACCESSIBILITY ACCESSIBLE, "
           "PROCEEDINGS OF THE 2021 IEEE CCNC, PP. 1-6")
    out = normalize_cr(raw)
    assert ";" not in out
    assert out == ("RUBANO V., VITALI F., MAKING ACCESSIBILITY ACCESSIBLE, "
                   "PROCEEDINGS OF THE 2021 IEEE CCNC, PP. 1-6")
    assert count_refs(out) == 1


def test_book_reference_without_year_is_kept():
    raw = "Atkinson K.B.; Fryer J., Close Range Photogrammetry And Machine Vision"
    out = normalize_cr(raw)
    assert "Close Range Photogrammetry" in out and ";" not in out


def test_bare_year_fragment_does_not_crash():
    """Gerçek Scopus verisinde 559 adet çıplak '(yyyy)' parçası var."""
    assert normalize_cr("(2021)") == "(2021)"
    cell = "PIVAC D., A TITLE, LAND, 10, 9, (2021); (2020)"
    out = normalize_cr(cell)
    assert out == "PIVAC D, 2021, LAND, V10; (2020)"
    assert normalize_cr(out) == out


# ── Birim: idempotency ve güvenli girdiler ───────────────────────────────

@pytest.mark.parametrize("raw", [r for r, _ in REAL_NEW_FORMAT] + REAL_WOS_CELLS + [
    "(2021)",
    "RUBANO V.; VITALI F., MAKING ACCESSIBILITY ACCESSIBLE, PP. 1-6",
])
def test_idempotent(raw):
    once = normalize_cr(raw)
    assert normalize_cr(once) == once


def test_empty_nan_and_non_string_input():
    assert normalize_cr("") == ""
    assert normalize_cr("   ") == ""
    assert normalize_cr(None) == ""
    assert normalize_cr(float("nan")) == ""
    assert normalize_cr("nan") == ""
    # Sayısal hücre → çökmeden dizeye çevrilir
    assert normalize_cr(2020) == "2020"


def test_count_refs():
    assert count_refs("") == 0
    assert count_refs(None) == 0
    assert count_refs(float("nan")) == 0
    assert count_refs("A, 2020, X") == 1
    assert count_refs("A, 2020, X; B, 2021, Y; C, 2022, Z") == 3
    # Boş parçalar sayılmaz
    assert count_refs("A, 2020, X; ; B, 2021, Y;") == 2


# ── Entegrasyon: wos export'u WoS dilbilgisi yazar ───────────────────────

SCOPUS_CR = (
    "FAHLE L.; HOLLEY E.A.; WALTON G., ANALYSIS OF SLAM-BASED LIDAR DATA, "
    "MIN METALL EXPLOR, 39, 5, PP. 1939-1960, (2022); "
    "PIVAC D., AVAILABILITY OF HISTORICAL CADASTRAL DATA, LAND, 10, 9, (2021); "
    "ALSTON B.; HARRELL J.; SHAW I., ANCIENT EGYPTIAN MATERIALS, (2000)"
)
WOS_CR = (
    "MILDENHALL B, 2022, COMMUN ACM, V65, P99, DOI 10.1145/3503250;"
    "PASZKE A, 2019, ADV NEUR IN, V32"
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


def _cr_blocks(text: str) -> list[list[str]]:
    """WoS metnindeki her kaydın CR bloğunu (devam satırları dâhil) döndür."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("CR "):
            current = [line[3:].strip()]
            blocks.append(current)
        elif current is not None and line.startswith("   "):
            current.append(line.strip())
        elif current is not None:
            current = None
    return blocks


def test_wos_export_writes_wos_grammar_cr_and_fills_nr(client):
    from services import analyses, dataset_io
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "CRExport"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    dataset_io.atomic_write_dataset(pd.DataFrame({
        "AU": ["SMITH, J", "DOE, A"],
        "TI": ["scopus sourced", "wos sourced"],
        "PY": [2023, 2023],
        "SO": ["J DOC", "J DOC"],
        "CR": [SCOPUS_CR, WOS_CR],
        "NR": ["", ""],          # boş NR — CR'den doldurulmalı
    }), adir / "merged.parquet")
    analyses.finalize_analysis(pid, aid)
    _DF_CACHE.clear()

    r = client.post(f"/api/projects/{pid}/export", json={"fmt": "wos"})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    dl = client.get(f"/api/projects/{pid}/download/exports/{name}")
    assert dl.status_code == 200
    text = dl.content.decode("utf-8")

    blocks = _cr_blocks(text)
    assert len(blocks) == 2
    scopus_refs, wos_refs = blocks
    assert len(scopus_refs) == 3 and len(wos_refs) == 2

    # 1) Scopus dilbilgisi kalmamalı: hiçbir yerde "(yyyy);" ya da satır sonunda
    #    "(yyyy)" olmamalı.
    assert re.search(r"\(\d{4}\)\s*;", text) is None
    for refs in blocks:
        for ref in refs:
            assert not re.search(r"\(\d{4}\)\s*$", ref), ref

    # 2) Her CR satırı ya WoS biçiminde ya da ';'siz nötrleştirilmiş olmalı.
    wos_shaped = re.compile(r"^[^,;]+,\s*(?:1[5-9]|20)\d{2}(?:,|$)")
    for refs in blocks:
        for ref in refs:
            assert ";" not in ref, ref
            assert wos_shaped.match(ref), ref

    assert scopus_refs == [
        "FAHLE L, 2022, MIN METALL EXPLOR, V39, P1939",
        "PIVAC D, 2021, LAND, V10",
        "ALSTON B, 2000",
    ]
    # WoS kaynaklı satır olduğu gibi yazılmalı
    assert wos_refs == [
        "MILDENHALL B, 2022, COMMUN ACM, V65, P99, DOI 10.1145/3503250",
        "PASZKE A, 2019, ADV NEUR IN, V32",
    ]

    # 3) CR'si dolu her kayıt için NR boş olmamalı ve referans sayısını vermeli.
    nr_values = [line[3:].strip() for line in text.splitlines() if line.startswith("NR ")]
    assert nr_values == ["3", "2"]
