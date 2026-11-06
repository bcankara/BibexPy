"""Cancellation and cost of the O(n²) similarity loops in disambiguation blocking.

One affiliation block can hold thousands of addresses (every "Department of ..." address
shares the block "department"). Before, should_cancel() was asked only between blocks, so
a cancelled scan kept a core busy for minutes, and the cohesion test of a cluster compared
all n²/2 pairs although only "every pair ≥ threshold?" was used. These tests pin that the
loops stop soon after cancellation and that the faster code gives the same answers as the
previous implementation (kept here as reference copies).
"""

import random
import string
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

from services.disambiguation import affiliation_graph, blocking  # noqa: E402
from services.disambiguation.similarity import jaro_winkler  # noqa: E402


# ── reference copies of the previous implementation ─────────────────────────

def _ref_min_pairwise_at_least(norms, threshold):
    """Previous cohesion test: min over all pairs, starting from 1.0, compared to threshold."""
    items = sorted(norms)
    lo = 1.0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            lo = min(lo, jaro_winkler(items[i], items[j]))
    return lo >= threshold


def _ref_greedy_cluster(members, threshold):
    """Previous _greedy_cluster (any() over the cluster members)."""
    clustered = []
    for m in members:
        if m.get("suffix"):
            clustered.append([m])
            continue
        placed = False
        for cluster in clustered:
            if any(x.get("suffix") for x in cluster):
                continue
            if any(jaro_winkler(m["norm"], x["norm"]) >= threshold for x in cluster):
                cluster.append(m)
                placed = True
                break
        if not placed:
            clustered.append([m])
    return clustered


def _ref_field_signal(members):
    """Previous _field_signal (all member pairs)."""
    sets = [m.get("fields") or set() for m in members]
    if not any(sets):
        return None
    n = len(members)
    for i in range(n):
        for j in range(i + 1, n):
            if sets[i] & sets[j]:
                return True
    return False


# ── helpers ─────────────────────────────────────────────────────────────────

def _department_frame(n: int) -> pd.DataFrame:
    """n distinct addresses that all fall into one block of each builder: "department" for
    the affiliation blocks (first word of the address), "zeta" for the organization rollup
    (first word of the parsed institution)."""
    rng = random.Random(7)
    words = ["".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(5, 10))) for _ in range(400)]
    rows = []
    for i in range(n):
        topic = " ".join(rng.sample(words, 2)).title()
        place = rng.choice(words).title()
        rows.append(f"[A] Department of {topic}, Zeta {place} University, {place} City, Country {i}")
    return pd.DataFrame({"UID": list(range(1, n + 1)), "C1": rows, "AU": ["SMITH J"] * n})


class _CancelAfter:
    """should_cancel() that turns True on its `calls`-th call and remembers when."""

    def __init__(self, calls: int):
        self.calls, self.n, self.at = calls, 0, None

    def __call__(self) -> bool:
        self.n += 1
        if self.n >= self.calls:
            if self.at is None:
                self.at = time.perf_counter()
            return True
        return False


# ── cancellation ────────────────────────────────────────────────────────────

def test_cancel_stops_inside_a_large_block():
    # 450 rows: 2 checks in the row loop, 1 before the single block; the 20th call can only
    # come from inside the similarity loops of that block (17 × 256 comparisons in). At 0.99
    # no two addresses are similar enough, so the greedy run would make ~100,000 comparisons
    # (at 0.75 these addresses chain into one cluster after one comparison each). Kept small
    # so that a regression fails within seconds instead of running the whole block for minutes.
    df = _department_frame(450)
    cancel = _CancelAfter(20)
    out = blocking.build_org_rollup(df, 0.99, "auto", 0.995, should_cancel=cancel)
    done = time.perf_counter()
    assert isinstance(out, list)
    assert cancel.at is not None, "should_cancel() was never asked inside the block"
    # Without checks inside the similarity loops this block runs to its end.
    assert done - cancel.at < 1.0, f"returned {done - cancel.at:.2f}s after cancellation"


def test_affiliation_cancel_stops_inside_the_pair_loop():
    # 250 addresses share the blocking key (country, "southern"): 31,125 pairs, one check per
    # _PAIR_CHECK_EVERY of them. The row loop and the signature-group loop make one check each
    # (rows / groups 0), so the 5th call can only come from inside the pair loop (3 checks in).
    # Without the pair-loop check the later loops make fewer than 5 calls and never cancel.
    rng = random.Random(21)
    names = ["".join(rng.choice(string.ascii_lowercase) for _ in range(9)).title() for _ in range(250)]
    rows = [f"[A] University of Southern {nm}, Zed, Germany" for nm in names]
    df = pd.DataFrame({"UID": list(range(1, len(rows) + 1)), "C1": rows})
    assert len(rows) * (len(rows) - 1) // 2 >= 4 * affiliation_graph._PAIR_CHECK_EVERY
    cancel = _CancelAfter(5)
    out = blocking.build_affiliation_blocks(df, should_cancel=cancel)
    done = time.perf_counter()
    assert out == []
    assert cancel.at is not None, "should_cancel() was never asked inside the pair loop"
    assert cancel.n == 5
    assert done - cancel.at < 1.0, f"returned {done - cancel.at:.2f}s after cancellation"
    # the same data without cancellation completes
    assert isinstance(blocking.build_affiliation_blocks(df), list)


def test_cancel_stops_country_builder():
    # 3,000 distinct unresolved country tokens: one greedy run over all of them
    # (12 checks in the row loop, 1 before the greedy run).
    rng = random.Random(3)
    toks = ["".join(rng.choice(string.ascii_lowercase) for _ in range(12)) for _ in range(3000)]
    df = pd.DataFrame({"UID": list(range(1, len(toks) + 1)),
                       "C1": [f"[A] Some Org, Some City, {t.title()}" for t in toks]})
    cancel = _CancelAfter(30)
    out = blocking.build_country_blocks(df, 0.75, "auto", 0.95, should_cancel=cancel)
    done = time.perf_counter()
    assert out == []
    assert cancel.at is not None and done - cancel.at < 1.0


def test_cancel_stops_cohesion_test_of_a_tight_cluster():
    # A cohesive cluster cannot stop early (every pair passes): all n²/2 comparisons unless
    # the guard interrupts. 400 norms → 79,800 pairs; cancelled after 5 × 256 of them.
    norms = {f"department of topic {i:04d}" for i in range(400)}
    cancel = _CancelAfter(5)
    calls = 0
    real = blocking.jaro_winkler

    def counting(a, b, *k):
        nonlocal calls
        calls += 1
        return real(a, b, *k)

    blocking.jaro_winkler = counting
    try:
        with pytest.raises(blocking._Cancelled):
            blocking._all_pairs_at_least(norms, 0.0, blocking._PairGuard(cancel))
    finally:
        blocking.jaro_winkler = real
    assert cancel.n == 5
    assert 4 * 256 <= calls < 5 * 256


def test_no_cancel_callback_runs_to_the_end():
    df = _department_frame(60)
    assert blocking.build_affiliation_blocks(df, 0.75, "auto", 0.95) == \
        blocking.build_affiliation_blocks(df, 0.75, "auto", 0.95, should_cancel=lambda: False)


# ── same answers as the previous implementation ─────────────────────────────

def test_all_pairs_at_least_matches_previous_min_pairwise():
    rng = random.Random(11)
    base = ["department of computer science", "dept of computer sci", "school of medicine",
            "univ ankara", "ankara univ", "istanbul tech univ", "a", "", "ab"]
    for _ in range(300):
        norms = set(rng.sample(base, rng.randint(0, 5)))
        for t in (0.0, 0.5, 0.75, 0.9, 0.95, 1.0, 1.01):
            expected = _ref_min_pairwise_at_least(norms, t)
            assert blocking._all_pairs_at_least(norms, t) == expected, (norms, t)
            guard = blocking._PairGuard(lambda: False)
            assert blocking._all_pairs_at_least(norms, t, guard) == expected, (norms, t)


def test_all_pairs_at_least_stops_at_first_failing_pair():
    calls = 0
    real = blocking.jaro_winkler

    def counting(a, b, *k):
        nonlocal calls
        calls += 1
        return real(a, b, *k)

    norms = {f"department of topic {i:04d} university {i * 7919 % 10007}" for i in range(400)}
    blocking.jaro_winkler = counting
    try:
        assert blocking._all_pairs_at_least(norms, 0.99) is False
    finally:
        blocking.jaro_winkler = real
    assert 1 <= calls < 1000  # all pairs would be 79,800


@pytest.mark.parametrize("threshold", [0.0, 0.6, 0.75, 0.8, 0.9, 1.0, 1.01])
def test_greedy_cluster_matches_previous_implementation(threshold):
    rng = random.Random(5)
    vocab = ["univ", "dept", "ankara", "ankra", "tech", "med", ""]
    for _ in range(20):
        members = [{"norm": " ".join(rng.choice(vocab) for _ in range(rng.randint(0, 4))).strip(),
                    "suffix": rng.choice(["", "", "", "", "b", "7"])} for _ in range(rng.randint(0, 60))]
        expected = [[id(m) for m in c] for c in _ref_greedy_cluster(members, threshold)]
        assert [[id(m) for m in c] for c in blocking._greedy_cluster(members, threshold)] == expected
        guard = blocking._PairGuard(lambda: False)
        assert [[id(m) for m in c] for c in blocking._greedy_cluster(members, threshold, guard)] == expected


def test_field_signal_matches_previous_implementation():
    rng = random.Random(9)
    tokens = ["cs", "math", "bio", "med", "eng"]
    for _ in range(2000):
        members = []
        for _ in range(rng.randint(0, 6)):
            m = {}
            r = rng.random()
            if r < 0.15:
                pass                      # no "fields" key
            elif r < 0.3:
                m["fields"] = set()       # empty
            elif r < 0.35:
                m["fields"] = None
            else:
                m["fields"] = set(rng.sample(tokens, rng.randint(1, 3)))
            members.append(m)
        assert blocking._field_signal(members) is _ref_field_signal(members), members


def test_field_signal_is_linear():
    # 20,000 mentions whose field sets never overlap: the pairwise version needs ~2·10⁸
    # set intersections (minutes); one pass over the tokens is instant.
    members = [{"fields": {f"field{i}"}} for i in range(20000)]
    start = time.perf_counter()
    assert blocking._field_signal(members) is False
    assert time.perf_counter() - start < 1.0
