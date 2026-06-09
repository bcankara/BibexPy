"""Regresyon: disambiguation pipeline event loop'u bloklamamalı (#7 freeze kök neden).

ORCID çözümü (orcids_for_candidate → fetch_orcids_for_doi → requests) SENKRON ağ
I/O yapar. Async worker içinde await'siz çağrılırsa event loop bloklanır → scan
sırasında cancel/navigasyon/export DONAR. Bu yüzden ORCID çağrıları yalnız
asyncio.to_thread ile çalıştırılan yardımcı fonksiyonlarda (_resolve_*) olmalı,
async fonksiyon gövdesinde DOĞRUDAN değil.
"""

import inspect
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def pipeline():
    api_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(api_root))
    for mod in list(sys.modules):
        if mod.startswith(("services", "config", "jobs")):
            sys.modules.pop(mod, None)
    from services.disambiguation import pipeline as p
    return p


def test_orcid_not_called_directly_on_event_loop(pipeline):
    """run_author_disambiguation ve _compute_author_splits ORCID'i DOĞRUDAN çağırmamalı."""
    for fn in (pipeline.run_author_disambiguation, pipeline._compute_author_splits):
        src = inspect.getsource(fn)
        assert "orcids_for_candidate" not in src, (
            f"{fn.__name__} orcids_for_candidate'i doğrudan çağırıyor → event loop bloklanır (#7)"
        )


def test_threaded_orcid_helpers_exist_and_used(pipeline):
    """Senkron ORCID I/O thread'e alan yardımcılar var ve kullanılıyor olmalı."""
    assert hasattr(pipeline, "_resolve_member_orcid_sets")
    assert hasattr(pipeline, "_resolve_split_group_sets")
    auth_src = inspect.getsource(pipeline.run_author_disambiguation)
    split_src = inspect.getsource(pipeline._compute_author_splits)
    assert "_resolve_member_orcid_sets" in auth_src
    assert "_resolve_split_group_sets" in split_src
    # Yardımcılar gerçekten ORCID çözüyor (içlerinde çağrı var)
    for h in (pipeline._resolve_member_orcid_sets, pipeline._resolve_split_group_sets):
        assert "orcids_for_candidate" in inspect.getsource(h)
