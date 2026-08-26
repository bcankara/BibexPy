"""v1-compat merge path (``bibex_core.MergeDB.merge_db_sources``) regressions.

The server pipeline uses ``services.smart_merger``, but the packaged
``bibex_core`` module is still what headless users import from a notebook or a
script, so the defects fixed here only ever showed up outside the web UI:

* the title key was built without lowercasing, so ``Deep Learning`` and
  ``Deep learning`` produced two keys and the same article survived twice;
* dedup grouped on the RAW ``DI`` value, so case, ``https://doi.org/`` prefixes
  and ``_`` vs ``-`` separator spellings each split one article into two groups;
* Scopus-grammar ``CR`` was written out unconverted, so those records silently
  dropped out of every coupling network downstream (84 of 436 records in the
  reported corpus).

``merge_db_sources`` itself performs no file I/O — only ``MergeDB.main()``
writes xlsx — so these tests call it directly on in-memory frames.
"""

import re
import sys
from pathlib import Path

import pandas as pd

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

from bibex_core.MergeDB import merge_db_sources  # noqa: E402
from bibex_core.cr_normalize import count_refs  # noqa: E402


# Gerçek Scopus "new format" CR — yazarlar ';' ile, referans sonu "(yyyy)".
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


def _wos(**overrides) -> pd.DataFrame:
    row = {
        "DB": "ISI",
        "AU": "SMITH J",
        "AF": "SMITH, JOHN",
        "TI": "Deep Learning In Bibliometrics",
        "PY": 2020,
        "SO": "SCIENTOMETRICS",
        "RP": "",
        "CR": WOS_CR,
        "NR": 2,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _scopus(**overrides) -> pd.DataFrame:
    row = {
        "DB": "SCOPUS",
        "AU": "Smith, John",
        "AF": "SMITH, JOHN (58490132900)",
        "TI": "Deep learning in bibliometrics",
        "PY": 2020,
        "SO": "Scientometrics",
        "RP": "",
        "CR": SCOPUS_CR,
        "NR": "",
    }
    row.update(overrides)
    return pd.DataFrame([row])


# ── (a) Başlık anahtarı küçük harfe indirilir ─────────────────────────────

def test_case_differing_title_year_pair_dedupes():
    """'Deep Learning In Bibliometrics' ile 'Deep learning in bibliometrics'
    aynı kayıttır; lower() olmadan iki ayrı anahtar üretiliyor ve duplike
    çıktıya kalıyordu. DOI kolonu yok — yalnız başlık+yıl yolu sınanır."""
    merged = merge_db_sources(_wos(), _scopus())
    assert len(merged) == 1, merged[["TI", "PY"]].to_dict("records")


def test_different_titles_are_not_over_merged():
    """Kontrol: lower() gerçek farklı kayıtları birleştirmemeli."""
    merged = merge_db_sources(
        _wos(TI="Deep Learning In Bibliometrics"),
        _scopus(TI="Machine learning for citation networks"),
    )
    assert len(merged) == 2


def test_same_title_different_year_kept_separate():
    merged = merge_db_sources(_wos(PY=2020), _scopus(PY=2015))
    assert len(merged) == 2


# ── (b) DOI ayırıcı/önek/case varyantları tek gruba iner ──────────────────

def test_separator_variant_doi_pair_dedupes():
    """GERÇEK VAKA: WoS '10.4103/jgid.jgid_12_19', Scopus aynı makaleyi
    '10.4103/jgid.jgid-12-19' verir. Başlıklar burada bilerek FARKLI — tek
    birleştirici yol DOI anahtarıdır."""
    merged = merge_db_sources(
        _wos(TI="Global infection trends", DI="10.4103/jgid.jgid_12_19"),
        _scopus(TI="A completely unrelated heading here",
                DI="https://doi.org/10.4103/JGID.JGID-12-19"),
    )
    assert len(merged) == 1, merged[["TI", "DI"]].to_dict("records")


def test_distinct_dois_stay_separate():
    """Kontrol: gerçekten farklı DOI'ler (ve farklı başlıklar) ayrı kalır."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.4103/jgid.jgid_12_19"),
        _scopus(TI="Beta study", DI="10.4103/jgid.jgid_13_19"),
    )
    assert len(merged) == 2


def test_records_without_doi_still_reach_title_path():
    """DOI'si boş/geçersiz satırlar (anahtar None) eskisi gibi başlık+yıl
    yoluna düşmeli — DOI grubunda kaybolmamalı."""
    merged = merge_db_sources(_wos(DI=""), _scopus(DI="n/a"))
    assert len(merged) == 1


# ── (c) CR Scopus dilbilgisinden WoS dilbilgisine çevrilir, NR sayılır ────

def test_scopus_cr_is_rewritten_and_nr_recounted():
    """Tek kaynaklı (Scopus-only) merge: CR tam olarak WoS dilbilgisine çevrilir
    ve NR referans sayımından doldurulur.

    Tek kaynakta ``len(M['DB'].unique()) > 1`` yanlış olduğu için alan-birleştirme
    bloğu (dolayısıyla ``merge_references``) hiç çalışmaz; böylece bu test
    yalnızca CR normalizasyon adımını ölçer.
    """
    merged = merge_db_sources(_scopus(TI="Beta study", DI="10.1/beta"))
    assert len(merged) == 1

    row = merged.iloc[0]
    assert row["CR"] == (
        "FAHLE L, 2022, MIN METALL EXPLOR, V39, P1939; "
        "PIVAC D, 2021, LAND, V10; "
        "ALSTON B, 2000"
    )
    assert re.search(r"\(\d{4}\)\s*;", str(row["CR"])) is None
    assert re.search(r"\(\d{4}\)\s*$", str(row["CR"])) is None
    # NR boştu → CR'den sayılmalı
    assert int(row["NR"]) == count_refs(row["CR"]) == 3


def test_wos_cr_passes_through_untouched():
    merged = merge_db_sources(_wos(TI="Alpha study", DI="10.1/alpha"))
    row = merged.iloc[0]
    assert row["CR"] == WOS_CR
    assert int(row["NR"]) == 2


def test_mixed_source_output_carries_no_scopus_cr_grammar():
    """WoS+Scopus karışık merge: çıktının hiçbir yerinde Scopus dilbilgisi
    kalıntısı ('(yyyy);' ya da satır sonu '(yyyy)') olmamalı ve NR, CR'deki
    referans sayısını vermeli.

    NOT: bu dalda MergeDB'nin kendi ``merge_references`` adımı Scopus CR'ını
    ';' üzerinden bölerek (Scopus'ta ';' YAZAR ayırıcısıdır) referansları
    parçalayıp yeniden sıralar. Bu ayrı ve ÖNCEDEN VAR OLAN bir kusurdur;
    burada CR içeriğinin sırası/eşleşmesi değil, yalnız dilbilgisi ve NR
    tutarlılığı doğrulanır."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.1/alpha"),
        _scopus(TI="Beta study", DI="10.1/beta"),
    )
    assert len(merged) == 2

    cr_all = "; ".join(str(v) for v in merged["CR"])
    assert re.search(r"\(\d{4}\)\s*;", cr_all) is None, cr_all
    assert re.search(r"\(\d{4}\)\s*$", cr_all) is None, cr_all

    for _, row in merged.iterrows():
        assert int(row["NR"]) == count_refs(row["CR"])


def test_zero_nr_with_non_empty_cr_is_recounted():
    """NR=0 + dolu CR eski merge'lerin yanlış değeri; CR'den yeniden sayılır."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.1/alpha", NR=0),
        _scopus(TI="Beta study", DI="10.1/beta", NR=0),
    )
    for _, row in merged.iterrows():
        assert int(row["NR"]) == count_refs(row["CR"])
        assert int(row["NR"]) > 0


def test_empty_cr_keeps_nr_untouched():
    """Gerçekten referanssız kayıtta (CR boş) NR'ye dokunulmaz."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.1/alpha", CR="", NR=0),
        _scopus(TI="Beta study", DI="10.1/beta", CR="", NR=""),
    )
    alpha = merged[merged["TI"].astype(str).str.contains("Alpha")].iloc[0]
    assert str(alpha["NR"]).strip() in ("0", "0.0")


def test_nr_column_created_when_absent():
    """Scopus export'unda NR kolonu yoktur; CR varsa NR üretilmeli."""
    w = _wos(TI="Alpha study", DI="10.1/alpha").drop(columns=["NR"])
    s = _scopus(TI="Beta study", DI="10.1/beta").drop(columns=["NR"])
    merged = merge_db_sources(w, s)
    assert "NR" in merged.columns
    for _, row in merged.iterrows():
        assert int(row["NR"]) == count_refs(row["CR"])


# ── (d) Yardımcı/geçici kolonlar çıktıda olmamalı ─────────────────────────

_TEMP_COLS = {"_doi_key", "clean_title", "title_year", "RP_WOS", "RP_SCOPUS"}


def test_helper_columns_absent_from_output():
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.4103/jgid.jgid_12_19"),
        _scopus(TI="Beta study", DI="https://doi.org/10.4103/JGID.JGID-13-19"),
    )
    assert _TEMP_COLS.isdisjoint(merged.columns), sorted(set(merged.columns) & _TEMP_COLS)


def test_helper_columns_absent_without_merge_fields():
    """merge_fields=False dalında da yardımcı kolon sızmamalı."""
    merged = merge_db_sources(
        _wos(TI="Alpha study", DI="10.4103/jgid.jgid_12_19"),
        _scopus(TI="Beta study", DI="https://doi.org/10.4103/JGID.JGID-12-19"),
        merge_fields=False,
    )
    assert len(merged) == 1
    assert _TEMP_COLS.isdisjoint(merged.columns), sorted(set(merged.columns) & _TEMP_COLS)


def test_no_dedup_path_still_normalizes_cr():
    """remove_duplicated=False iken de CR/NR düzeltmesi uygulanır."""
    merged = merge_db_sources(_wos(), _scopus(), remove_duplicated=False)
    assert len(merged) == 2
    assert re.search(r"\(\d{4}\)\s*;", "; ".join(str(v) for v in merged["CR"])) is None
    assert _TEMP_COLS.isdisjoint(merged.columns)


def test_merge_references_does_not_shred_raw_scopus_cr():
    """5. kusur: merge_references ham Scopus CR'ı çıplak ';' ile bölüp yazar
    kırıntılarına parçalıyordu (yazar↔referans eşlemesi bozuluyordu). Artık
    girişte normalize eder — çıktıda kırıntı yok, 2 referans 2 kalır."""
    from bibex_core.MergeDB import merge_references

    scopus_cr = ("FAHLE L.; HOLLEY E.A.; WALTON G., ANALYSIS OF SLAM DATA, "
                 "MIN METALL EXPLOR, 39, 5, PP. 1939-1960, (2022); "
                 "PIVAC D.; MARTIN R., ANOTHER TITLE, AUTOM CONSTR, 119, (2021)")
    out = merge_references("", scopus_cr)
    refs = [r.strip() for r in out.split(";") if r.strip()]
    assert len(refs) == 2, refs
    assert any(r.startswith("FAHLE L, 2022") for r in refs), refs
    assert any(r.startswith("PIVAC D, 2021") for r in refs), refs
    # Yazar kırıntısı tek başına referans olmamalı
    assert not any(r in ("HOLLEY E.A.", "WALTON G.", "MARTIN R.") for r in refs)

    # WoS girdisiyle birleşim: WoS referansı aynen korunur
    wos_cr = "SMITH J, 2020, J DOC, V66, P214"
    both = merge_references(wos_cr, scopus_cr)
    assert "SMITH J, 2020, J DOC, V66, P214" in both
    assert len([r for r in both.split(";") if r.strip()]) == 3
