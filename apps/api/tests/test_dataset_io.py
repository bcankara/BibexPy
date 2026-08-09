"""Tests for the Parquet working format (services.dataset_io).

The active dataset and its snapshots are stored as Parquet; Excel survives only
behind explicit export paths. These tests cover the round-trip, the mixed-dtype
coercion fallback, the one-time lazy migration of legacy merged.xlsx datasets,
restoring an xlsx-era snapshot onto a Parquet dataset, and the failure modes
that must degrade quietly (unreadable dataset -> 409, unreadable legacy file ->
no migration, no exception).
"""

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIBEXPY_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "routers", "services", "models", "jobs")):
            sys.modules.pop(mod, None)
    from main import app
    return TestClient(app)


# ── T1: parquet roundtrip ────────────────────────────────────────────────

def test_parquet_roundtrip(tmp_path):
    from services import dataset_io

    p = tmp_path / "merged.parquet"
    df = pd.DataFrame({"TI": ["a", "b"], "PY": [2020, 2021]})
    dataset_io.atomic_write_dataset(df, p)
    assert p.exists()

    back = dataset_io.read_dataset(p)
    assert len(back) == 2
    assert list(back["TI"]) == ["a", "b"]
    assert list(back["PY"]) == [2020, 2021]
    assert str(back["PY"].dtype).startswith("int")  # dtype korunur (xlsx'te de öyle)
    # Geçici dosya artığı kalmamalı
    assert not list(tmp_path.glob("*.tmp~"))


# ── T2: karışık-tip coercion, girdi df MUTASYONSUZ ───────────────────────

def test_mixed_dtype_coercion_does_not_mutate_input(tmp_path):
    """Arrow karışık-tipli object kolonu reddeder → önce SAYISAL geri kazanım
    (read_excel'in eskiden her okumada yaptığı re-inference'ın karşılığı),
    sayısal olamıyorsa temiz string'leme.

    Girdi frame'i filter_engine cache'indeki frame olabilir; bozulması sonraki
    tüm okumaları zehirlerdi.
    """
    from services import dataset_io

    df = pd.DataFrame({"PY": [2020, "2021", None]})
    coerced = dataset_io._coerce_for_parquet(df)
    # Sayısala çevrilebilen karışık kolon SAYISAL kalır — string'e düşmez
    assert str(coerced["PY"].dtype).startswith("float")
    assert list(coerced["PY"].dropna()) == [2020.0, 2021.0]
    # Girdi df dokunulmamış olmalı
    assert df["PY"].iloc[0] == 2020 and df["PY"].iloc[2] is None

    # Yazma yolu da aynı frame'i kabul etmeli (doğrudan to_parquet ArrowInvalid verir)
    p = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(df, p)
    back = dataset_io.read_dataset(p)
    assert str(back["PY"].dtype).startswith("float")
    assert df["PY"].iloc[0] == 2020  # yazım sonrası da mutasyon yok
    assert not list(tmp_path.glob("*.tmp~"))


def test_coercion_no_float_repr_artifacts(tmp_path):
    """Float üyeli, sayısala ÇEVRİLEMEYEN karışık kolon string'lenirken
    "2020.0" artefaktı bırakmamalı; ve Arrow'un ZATEN kabul ettiği kolonlara
    (saf sayısal object) coercion sıçramamalı."""
    from services import dataset_io

    df = pd.DataFrame({
        "BAD": pd.Series([1, "x", None], dtype="object"),      # yazımı düşüren kolon
        "MIXF": pd.Series([2020.0, "abc", None], dtype="object"),  # float + str, sayısal olamaz
        "TCNUM": pd.Series([1, 2.5, 3], dtype="object"),       # saf sayısal object (Arrow kabul eder)
    })
    p = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(df, p)
    back = dataset_io.read_dataset(p)

    assert list(back["BAD"]) == ["1", "x", ""]
    # float 2020.0 → "2020", "2020.0" DEĞİL (xlsx döneminde sayı olarak görünürdü)
    assert list(back["MIXF"].dropna().astype(str))[:1] == ["2020"]
    assert "2020.0" not in back["MIXF"].astype(str).tolist()
    # Kolateral hasar yok: başka kolon düştü diye sayısal kolon string olmaz
    assert str(back["TCNUM"].dtype).startswith("float")
    assert list(back["TCNUM"]) == [1.0, 2.5, 3.0]


def test_write_survives_bigint_and_duplicate_columns(tmp_path):
    """to_excel'in tolere ettiği iki uç durum yazımı düşürmemeli: 2^63 üstü
    Python int'leri (OverflowError) ve duplike kolon etiketleri (pyarrow
    ValueError; eski coercion içinde AttributeError'a dönüşüyordu)."""
    from services import dataset_io

    p1 = tmp_path / "big.parquet"
    dataset_io.atomic_write_dataset(
        pd.DataFrame({"HUGE": pd.Series([2**70, 2**71], dtype="object")}), p1)
    assert len(dataset_io.read_dataset(p1)) == 2

    p2 = tmp_path / "dup.parquet"
    dup = pd.DataFrame([[1, "a"], [2, "b"]])
    dup.columns = ["X", "X"]
    dataset_io.atomic_write_dataset(dup, p2)
    back = dataset_io.read_dataset(p2)
    assert list(back.columns) == ["X", "X.1"]  # read_excel'in mangling'iyle aynı
    assert not list(tmp_path.glob("*.tmp~"))


def test_coercion_leaves_numeric_columns_alone(tmp_path):
    """Sayısal kolonlar ASLA string'e çevrilmez (PY/TC tüketicileri numeriktir)."""
    from services import dataset_io

    df = pd.DataFrame({"PY": [2020, 2021], "MIX": [1, "x"]})
    coerced = dataset_io._coerce_for_parquet(df)
    assert list(coerced["PY"]) == [2020, 2021]
    assert str(coerced["PY"].dtype).startswith("int")
    assert list(coerced["MIX"]) == ["1", "x"]


# ── T3: bozuk parquet → 409 (500 değil) ──────────────────────────────────

def test_corrupted_parquet_returns_409_not_500(client):
    """Yarım/bozuk merged.parquet → 500 yerine temiz 409 (snapshot ipucuyla)."""
    from services import analyses
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "CorruptParquet"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    (adir / "merged.parquet").write_bytes(b"\x00\x01half-written garbage")
    analyses.finalize_analysis(pid, aid)
    _DF_CACHE.clear()

    r = client.post(f"/api/projects/{pid}/filter", json={"spec": {}, "limit": 1})
    assert r.status_code == 409, r.text
    assert "unreadable" in r.json()["detail"]


# ── T4: tembel migrasyon (gerçek API çağrısıyla) ─────────────────────────

def test_lazy_migration_on_first_api_call(client):
    """Legacy merged.xlsx: ilk istekte parquet oluşur, xlsx analiz klasöründen
    çıkar ve snapshots/ altına pre_parquet_migration_*.xlsx olarak taşınır."""
    from services import analyses
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "Legacy"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    pd.DataFrame({"TI": ["a", "b"], "PY": [2020, 2021]}).to_excel(
        adir / "merged.xlsx", index=False)
    analyses.finalize_analysis(pid, aid)
    _DF_CACHE.clear()

    r = client.post(f"/api/projects/{pid}/filter", json={"spec": {}, "limit": 5})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2

    assert (adir / "merged.parquet").exists()
    assert not (adir / "merged.xlsx").exists()
    snaps = list((adir / "snapshots").glob("pre_parquet_migration_*.xlsx"))
    assert len(snaps) == 1, [p.name for p in (adir / "snapshots").iterdir()]
    # Taşınan dosya orijinal xlsx byte'ları — hâlâ okunabilir olmalı
    assert len(pd.read_excel(snaps[0])) == 2
    assert not list(adir.glob("*.tmp~"))


# ── T5: xlsx dönemi snapshot'ının parquet dataset'e restore'u ────────────

def test_legacy_xlsx_snapshot_restores_onto_parquet_dataset(client):
    """İki restore yolu da (records endpoint'i + pipeline) format dönüştürür;
    ham byte kopyası merged.parquet'i bozardı."""
    from services import analyses, dataset_io, storage
    from services.filter_engine import _DF_CACHE

    pid = client.post("/api/projects", json={"name": "Restore"}).json()["id"]
    aid, adir = analyses.create_analysis(pid, "smart")
    target = adir / "merged.parquet"
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["x"], "PY": [2020]}), target)
    analyses.finalize_analysis(pid, aid)

    snaps = adir / "snapshots"
    snaps.mkdir(exist_ok=True)
    legacy = snaps / "pre_delete_20200101_000000.xlsx"
    pd.DataFrame({"TI": ["a", "b", "c"], "PY": [2001, 2002, 2003]}).to_excel(
        legacy, index=False)
    rel = str(legacy.relative_to(storage.settings.storage_path))
    _DF_CACHE.clear()

    # Legacy snapshot listede görünmeli (xlsx + parquet birlikte)
    listed = client.get(f"/api/projects/{pid}/records/snapshots").json()
    assert legacy.name in [it["name"] for it in listed]

    # 1) records endpoint'i
    r = client.post(f"/api/projects/{pid}/records/restore-snapshot", json={"snapshot": rel})
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 3
    assert target.exists() and len(dataset_io.read_dataset(target)) == 3

    # 2) disambiguation pipeline yolu
    from services.disambiguation import pipeline

    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["y"], "PY": [2020]}), target)
    _DF_CACHE.clear()
    out = pipeline.restore_snapshot(pid, rel)
    assert out["restored_from"] == rel
    back = dataset_io.read_dataset(target)  # geçerli parquet olarak okunabilmeli
    assert len(back) == 3 and list(back["TI"]) == ["a", "b", "c"]


# ── T6: None/NaN normalizasyon regresyonu ────────────────────────────────

def test_missing_values_read_back_as_empty_string(tmp_path):
    """Parquet NA'yı None döndürür; `astype(str).ne("NAN")` gibi boşluk
    kontrolleri "None"u yakalayamaz → DOI'siz kayıtlar DOI'liymiş gibi
    işlenirdi. read_dataset her iki formatta da "" garanti eder."""
    from services import dataset_io

    df = pd.DataFrame({"DI": ["10.1/a", None, ""], "AB": [None, None, "text"]})
    p = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(df, p)

    back = dataset_io.read_dataset(p)
    assert list(back["DI"]) == ["10.1/a", "", ""]
    assert list(back["AB"]) == ["", "", "text"]
    assert "None" not in back["DI"].astype(str).tolist()
    # Üretimdeki boşluk testi (enricher._api_pass) doğru saymalı: 1 DOI'li kayıt
    with_doi = back[back["DI"].astype(str).str.strip().ne("")
                    & back["DI"].astype(str).str.upper().ne("NAN")]
    assert len(with_doi) == 1


# ── T7: oku-hemen-değiştir (Windows dosya kilidi) ────────────────────────

def test_read_then_immediately_replace(tmp_path):
    """Okuma dosyayı açık tutmamalı (memory_map / ParquetFile handle'ı yok);
    aksi halde os.replace Windows'ta PermissionError verirdi."""
    from services import dataset_io

    p = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": list("abcdef")}), p)
    for _ in range(5):
        df = dataset_io.read_dataset(p)
        dataset_io.atomic_write_dataset(df.iloc[:-1].reset_index(drop=True), p)
    assert len(dataset_io.read_dataset(p)) == 1
    assert not list(tmp_path.glob("*.tmp~"))


# ── T8: ensure_parquet idempotent ────────────────────────────────────────

def test_ensure_parquet_is_idempotent(tmp_path):
    from services import dataset_io

    src = tmp_path / "merged.xlsx"
    pd.DataFrame({"TI": ["a"], "PY": [2020]}).to_excel(src, index=False)

    first = dataset_io.ensure_parquet(src)
    assert first == tmp_path / "merged.parquet" and first.exists()
    assert not src.exists()
    migrations = list((tmp_path / "snapshots").glob("pre_parquet_migration_*.xlsx"))
    assert len(migrations) == 1

    # Parquet yolu → aynen döner, ikinci migrasyon snapshot'ı YOK
    assert dataset_io.ensure_parquet(first) == first
    # Eski xlsx yolu tekrar sorulsa bile hedef zaten var → onu döndürür
    assert dataset_io.ensure_parquet(src) == first
    assert len(list((tmp_path / "snapshots").glob("pre_parquet_migration_*.xlsx"))) == 1
    assert not list(tmp_path.glob("*.tmp~"))


# ── T9: bozuk legacy xlsx'te migrasyon raise ETMEZ ───────────────────────

def test_ensure_parquet_on_unreadable_xlsx_returns_original(tmp_path):
    """Bozuk legacy dosyada migrasyon sessizce vazgeçer — 409 sözleşmesi
    (filter_engine) 500'e dönüşmemeli."""
    from services import dataset_io

    src = tmp_path / "merged.xlsx"
    src.write_bytes(b"\x00\x01half-written garbage")

    out = dataset_io.ensure_parquet(src)
    assert out == src and src.exists()
    assert not (tmp_path / "merged.parquet").exists()
    assert not list(tmp_path.glob("*.tmp~"))


# ── T10: boş DataFrame roundtrip ─────────────────────────────────────────

def test_empty_dataframe_roundtrip(tmp_path):
    from services import dataset_io

    p = tmp_path / "merged.parquet"
    df = pd.DataFrame({"TI": pd.Series(dtype="object"), "PY": pd.Series(dtype="int64")})
    dataset_io.atomic_write_dataset(df, p)

    back = dataset_io.read_dataset(p)
    assert len(back) == 0
    assert list(back.columns) == ["TI", "PY"]
    assert not list(tmp_path.glob("*.tmp~"))


# ── T11: eşzamanlı migrasyon TEK okuma yapar (lost-update yarışı kapalı) ──

def test_concurrent_migration_reads_legacy_exactly_once(tmp_path, monkeypatch):
    """İki thread aynı legacy xlsx'i aynı anda çözerse migrasyon kilit altında
    TEK SEFER koşmalı. Kilitsiz halde yavaş thread bayat frame'i sonradan
    yayınlayıp bu arada parquet'e inmiş bir mutasyonu sessizce geri alıyordu
    (thread'li repro ile doğrulanmış major bulgu)."""
    import threading as th
    from services import dataset_io

    src = tmp_path / "merged.xlsx"
    pd.DataFrame({"TI": ["a", "b"], "PY": [2020, 2021]}).to_excel(src, index=False)

    calls = {"n": 0}
    real_read = dataset_io.read_dataset

    def slow_read(path):
        calls["n"] += 1
        import time as _t
        _t.sleep(0.4)  # okuma penceresini büyüt — kilitsiz kodda 2. thread buraya girerdi
        return real_read(path)

    monkeypatch.setattr(dataset_io, "read_dataset", slow_read)

    results: list = [None, None]

    def worker(i):
        results[i] = dataset_io.ensure_parquet(src)

    t1 = th.Thread(target=worker, args=(0,))
    t2 = th.Thread(target=worker, args=(1,))
    t1.start(); t2.start(); t1.join(); t2.join()

    target = tmp_path / "merged.parquet"
    assert results[0] == target and results[1] == target
    assert calls["n"] == 1, "migrasyon kilidi ikinci okumayı engellemeli"
    assert len(real_read(target)) == 2
    assert not list(tmp_path.glob("*.tmp~"))


# ── T12: zombi legacy xlsx sonraki çözümlemede iyileştirilir ─────────────

def test_leftover_legacy_xlsx_is_healed_on_later_resolution(tmp_path):
    """İlk migrasyonda taşıma başarısız olursa (Excel'de açık dosya, AV taraması)
    xlsx parquet'in yanında kalıyordu — sonsuza dek: UI iki 'ana dataset' gösterir
    ve xlsx sessizce bayatlar. Sonraki ensure_parquet çağrısı taşımayı yeniden
    denemeli."""
    from services import dataset_io

    src = tmp_path / "merged.xlsx"
    pd.DataFrame({"TI": ["old"]}).to_excel(src, index=False)
    target = tmp_path / "merged.parquet"
    dataset_io.atomic_write_dataset(pd.DataFrame({"TI": ["new", "er"]}), target)
    # Durum: ikisi de yan yana (başarısız taşımanın kalıntısı)

    out = dataset_io.ensure_parquet(src)
    assert out == target
    assert not src.exists(), "zombi xlsx snapshots/ altına taşınmalıydı"
    moved = list((tmp_path / "snapshots").glob("pre_parquet_migration_*.xlsx"))
    assert len(moved) == 1 and len(pd.read_excel(moved[0])) == 1
    # Parquet içeriğine dokunulmamış olmalı (taşıma veri yazmaz)
    assert len(dataset_io.read_dataset(target)) == 2
