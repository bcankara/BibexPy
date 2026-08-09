"""Format-agnostic I/O for the active analysis dataset.

The internal working format is Parquet: reading and writing a 10k-row dataset
with openpyxl costs seconds, Parquet costs milliseconds, and every mutation
(record delete, filter apply, harmonization) pays that cost twice (snapshot +
dataset). Excel stays available, but only through the explicit export paths.

Every read/write of the active dataset goes through this module, so callers
never have to know which suffix is on disk, and legacy ``merged.xlsx`` datasets
are migrated lazily by :func:`ensure_parquet` the first time they are resolved.

This module imports only the standard library, pandas and pyarrow, on purpose:
the service layer uses flat imports and ``filter_engine`` already depends on
``merger``, so importing anything from ``services`` here would close a cycle.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

import pandas as pd


# Suffixes that may hold an active dataset, in resolution preference order.
DATASET_SUFFIXES = (".parquet", ".xlsx")

# os.replace can lose a race with a short concurrent read on Windows.
_REPLACE_ATTEMPTS = 6
_REPLACE_DELAY = 0.25

# A failed migration must not re-read the whole legacy workbook on every
# resolver call (that would add seconds to every request until it succeeds);
# retry at most once per this many seconds.
_MIGRATION_RETRY_INTERVAL = 60.0


# ─────────────────────────────────────────────────────────────
#  Reading
# ─────────────────────────────────────────────────────────────

def _normalize_object_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Replace NA in object columns with "" so blanks look identical per format.

    Parquet round-trips a missing object value as ``None`` while Excel yields
    ``NaN``. Blank checks downstream are string based (``astype(str)`` compared
    against ``""`` / ``"NAN"``), so an un-normalized ``None`` would read back as
    the literal ``"None"`` and count as a real value — e.g. records without a
    DOI would be treated as having one. Normalizing here covers both formats
    with one rule.
    """
    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].fillna("")
    return df


def read_dataset(path: Any) -> pd.DataFrame:
    """Read a dataset, dispatching on suffix, and normalize blank cells."""
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
    else:
        df = pd.read_excel(p)
    return _normalize_object_columns(df)


# ─────────────────────────────────────────────────────────────
#  Writing
# ─────────────────────────────────────────────────────────────

def _clean_cell(v: Any) -> str:
    """Stringify one cell the way the Excel round-trip used to render it.

    A bare ``str()`` would produce ``"nan"`` for NA and ``"2021.0"`` for an
    integral float — the latter then survives forever in Parquet where the old
    xlsx round-trip re-inferred it back to a number.
    """
    if pd.isna(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _coerce_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy in which exactly the Arrow-rejected columns are fixed.

    Only columns that pyarrow itself refuses are touched (probed per column
    with ``pa.array``): a numeric-capable object column elsewhere in the frame
    must not be collaterally stringified. A rejected column is first tried as
    numeric (restoring what ``read_excel`` used to re-infer — blanks become
    NaN); only if that fails is it rendered to strings via :func:`_clean_cell`.

    The input frame is never mutated: it may be the frame filter_engine handed
    out of its cache, and corrupting that would poison every later read.
    """
    import pyarrow as pa

    out = df.copy(deep=False)
    if out.columns.duplicated().any():
        # pyarrow refuses duplicate labels outright (to_excel tolerated them).
        # Mangle exactly like read_excel would on the next round-trip: A, A.1.
        seen: dict[str, int] = {}
        cols = []
        for c in map(str, out.columns):
            n = seen.get(c, 0)
            seen[c] = n + 1
            cols.append(c if n == 0 else f"{c}.{n}")
        out.columns = cols

    for i, col in enumerate(out.columns):
        s = out.iloc[:, i]
        if s.dtype != "object":
            continue
        try:
            pa.array(s, from_pandas=True)
            continue  # Arrow accepts this column as-is
        except Exception:
            pass
        # Numeric first: blanks count as NA, not as a conversion failure.
        try:
            out[col] = pd.to_numeric(s.where(s != "", other=None))
            continue
        except (ValueError, TypeError, OverflowError):
            pass
        out[col] = s.map(_clean_cell)
    return out


def atomic_write_dataset(df: pd.DataFrame, path: Any) -> None:
    """Write a dataset ATOMICALLY, dispatching on suffix.

    A direct write truncates the target first and then takes seconds to fill
    it; any request reading in that window (a quality/stats poll, say) sees a
    half-written file, and a process killed mid-write leaves it corrupt for
    good. Writing to a temp file and swapping it in with ``os.replace`` means
    readers always see either the whole old file or the whole new one, and an
    interruption leaves nothing but a ``.tmp~`` leftover.

    The temp name carries a per-call random token: with a fixed ``.tmp~`` name
    two concurrent writers share one temp file and can publish a half-written
    frame. On Windows a short concurrent read can still make ``os.replace``
    raise PermissionError, hence the bounded retry.
    """
    p = Path(path)
    tmp = p.with_name(f"{p.name}.{uuid4().hex[:8]}.tmp~")
    try:
        if p.suffix.lower() == ".parquet":
            try:
                df.to_parquet(tmp, index=False)
            except (ValueError, TypeError, OverflowError):
                # ArrowInvalid/ArrowTypeError subclass ValueError/TypeError;
                # OverflowError covers >64-bit Python ints. Retry once with
                # the offending columns coerced.
                _coerce_for_parquet(df).to_parquet(tmp, index=False)
        else:
            df.to_excel(tmp, index=False)
        last_err: BaseException | None = None
        for _ in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, p)
                return
            except PermissionError as e:  # target briefly open by a reader
                last_err = e
                time.sleep(_REPLACE_DELAY)
        raise last_err  # type: ignore[misc]
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────
#  Lazy migration
# ─────────────────────────────────────────────────────────────

# One lock per target path: the migration read of a big workbook takes seconds
# and FastAPI serves the first page-load's requests on parallel threads. An
# unlocked migration is a lost-update race: a slow thread's stale publish would
# silently roll back any mutation that landed on the fresh parquet meanwhile.
_MIGRATION_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

# Last failed attempt per source path → monotonic timestamp (see interval above).
_LAST_FAILURE: dict[str, float] = {}


def _migration_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _MIGRATION_LOCKS.setdefault(key, threading.Lock())


def _move_legacy_aside(p: Path) -> Optional[Path]:
    """Move the migrated workbook into snapshots/ (bounded retry, best effort).

    Returns the snapshot path, or None when the move could not be done (the
    file stays where it is and a later resolution retries — a stale twin next
    to the parquet would otherwise be listed as a second "main dataset" and
    silently diverge from the live data).
    """
    if not p.exists():
        return None
    try:
        snaps = p.parent / "snapshots"
        snaps.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
        dest = snaps / f"pre_parquet_migration_{stamp}{p.suffix.lower()}"
        for _ in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(p, dest)
                return dest
            except FileNotFoundError:
                return None  # a racing worker moved it first
            except PermissionError:
                time.sleep(_REPLACE_DELAY)
    except Exception:
        pass
    return None


def ensure_parquet(
    path: Any,
    on_migrate: Optional[Callable[[Path, Optional[Path]], None]] = None,
) -> Path:
    """Migrate a legacy .xlsx dataset to Parquet once, and return the path to use.

    Idempotent, race safe and NEVER raises: an unreadable legacy file must keep
    surfacing as the caller's clean 409 ("dataset unreadable"), not turn into a
    500 here. On any failure the original path is returned and the app keeps
    working off the .xlsx (retried at most every _MIGRATION_RETRY_INTERVAL so a
    persistent failure does not re-read the whole workbook on every request).

    The old workbook is moved (not re-encoded) into ``snapshots/`` so the
    original bytes survive as a rollback. ``on_migrate(target, snapshot)`` is
    called once after a successful migration — the caller can audit it there
    (this module cannot: it must not import services).
    """
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        return p
    target = p.with_suffix(".parquet")
    if target.exists():
        # Already migrated. Heal a leftover twin: if the xlsx is still here a
        # previous move-aside failed (file was open in Excel, AV scan, ...).
        _move_legacy_aside(p)
        return target

    lock = _migration_lock(str(target).lower())
    with lock:
        if target.exists():  # a worker ahead of us migrated while we waited
            _move_legacy_aside(p)
            return target
        last = _LAST_FAILURE.get(str(p).lower())
        if last is not None and time.monotonic() - last < _MIGRATION_RETRY_INTERVAL:
            return p
        try:
            df = read_dataset(p)
            atomic_write_dataset(df, target)
        except Exception:
            _LAST_FAILURE[str(p).lower()] = time.monotonic()
            return p
        _LAST_FAILURE.pop(str(p).lower(), None)
        snapshot = _move_legacy_aside(p)

    if on_migrate is not None:
        try:
            on_migrate(target, snapshot)
        except Exception:
            pass  # auditing must never break dataset resolution
    return target
