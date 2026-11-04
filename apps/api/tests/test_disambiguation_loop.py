"""Regression tests ensuring the disambiguation pipeline never blocks the event loop.

Synchronous ORCID network I/O must run only inside the threaded helpers
(``_resolve_*``) so it is offloaded via ``asyncio.to_thread``, and never be
called directly from the async pipeline functions.
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
    """run_author_disambiguation and _compute_author_splits must NOT call ORCID DIRECTLY."""
    for fn in (pipeline.run_author_disambiguation, pipeline._compute_author_splits):
        src = inspect.getsource(fn)
        assert "orcids_for_candidate" not in src, (
            f"{fn.__name__} calls orcids_for_candidate directly → the event loop gets blocked (#7)"
        )


def test_threaded_orcid_helpers_exist_and_used(pipeline):
    """The helpers that offload synchronous ORCID I/O to a thread must exist and be used."""
    assert hasattr(pipeline, "_resolve_member_orcid_sets")
    assert hasattr(pipeline, "_resolve_split_group_sets")
    auth_src = inspect.getsource(pipeline.run_author_disambiguation)
    split_src = inspect.getsource(pipeline._compute_author_splits)
    assert "_resolve_member_orcid_sets" in auth_src
    assert "_resolve_split_group_sets" in split_src
    # The helpers really do resolve ORCID (the call is inside them)
    for h in (pipeline._resolve_member_orcid_sets, pipeline._resolve_split_group_sets):
        assert "orcids_for_candidate" in inspect.getsource(h)
