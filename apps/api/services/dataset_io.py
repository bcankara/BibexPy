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

import asyncio
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
        s = df[col]
        # In pandas 3 text columns arrive with the `str` dtype; a check on
        # `dtype == "object"` alone would skip them and the blank normalization
        # would never be applied — the blank tests below would also treat NaN
        # as "filled".
        if s.dtype == "object" or pd.api.types.is_string_dtype(s):
            df[col] = s.fillna("").astype(object)
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
        # Numeric first: blanks count as NA, not as a conversion failure. The
        # result is probed with Arrow before it is accepted — pandas 2 turns
        # out-of-int64-range integers into float64 (silent precision loss) and
        # pandas 3 leaves them as exact Python ints that Arrow then refuses,
        # which used to crash the write. Anything Arrow will not take is
        # rendered to text instead, which always round-trips.
        try:
            candidate = pd.to_numeric(s.where(s != "", other=None))
            pa.array(candidate, from_pandas=True)
        except Exception:
            candidate = None
        out[col] = candidate if candidate is not None else s.map(_clean_cell)
    return out


# ─────────────────────────────────────────────────────────────
#  Row identity (UID)
# ─────────────────────────────────────────────────────────────
# Every row of a dataset carries a UID that BibexPy assigns once, when the dataset
# is born (merge, single-source analysis, legacy migration), and never changes.
# Every operation that refers to rows across requests (record edit/delete,
# disambiguation proposals, borderline decisions, provenance logs) refers to the
# UID, never to a row position: positions shift after any delete, filter or restore.
#
# The UID is a sequential integer 1..N (like a database primary key), int64 in the
# frame and in the parquet file. Rules:
#   * a key is issued once and never changes or is reused; deleting rows leaves gaps
#     (1, 2, 4, 5 ...) - renumbering would turn the key back into a row position;
#   * the analysis meta keeps a high-water mark (``uid_max``) of the largest key ever
#     issued, so a repair always issues keys above every key a snapshot may still hold;
#   * comparisons go through :func:`uid_key` (requests carry text, frames carry ints).

UID_COL = "UID"
_UID_BLANKS = frozenset({"", "nan", "none", "<na>"})

# Keys stay far below the int64 limit so uid_max + 1 can never overflow.
UID_LIMIT = 2 ** 62


def uid_key(value: Any) -> str:
    """Canonical text form of a UID for comparisons and lookups.

    Integer keys compare equal however they arrive: 12, 12.0, "12" and " 12 " all give "12"
    (a request carries strings, a parquet column int64, a float column after a concat).
    Any other text is returned stripped; blanks, None and NaN give "".
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:
            return ""
        if value.is_integer():
            return str(int(value))
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return str(value)
    try:
        import numpy as _np
        if isinstance(value, _np.integer):
            return str(int(value))
        if isinstance(value, _np.floating):
            f = float(value)
            return "" if f != f else (str(int(f)) if f.is_integer() else str(f))
    except ImportError:  # pragma: no cover
        pass
    text = str(value).strip()
    if text.lower() in _UID_BLANKS:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def uid_int(value: Any) -> Optional[int]:
    """The positive integer a UID value stands for, or None (blank, text, zero, negative)."""
    text = uid_key(value)
    if text and text.isascii() and text.isdigit():
        n = int(text)
        if 1 <= n < UID_LIMIT:
            return n
    return None


def _uid_text(df: pd.DataFrame) -> pd.Series:
    return df[UID_COL].map(uid_key)


def _uid_ints(df: pd.DataFrame) -> tuple[Any, Any]:
    """(values, valid): int64 array of the positive-integer keys (0 where none) and a bool mask."""
    import numpy as np

    n = len(df)
    if UID_COL not in df.columns:
        return np.zeros(n, dtype="int64"), np.zeros(n, dtype=bool)
    col = df[UID_COL]
    if col.dtype.kind in "iu":
        try:
            # A nullable Int64 column holding <NA>, or a uint64 value above the int64 range,
            # cannot take this fast path: it falls through to the per-value one below.
            values = col.to_numpy(dtype="int64", copy=True)
        except (ValueError, TypeError, OverflowError):
            values = None
        if values is not None:
            valid = (values >= 1) & (values < UID_LIMIT)
            values[~valid] = 0
            return values, valid
    values = np.zeros(n, dtype="int64")
    valid = np.zeros(n, dtype=bool)
    for pos, v in enumerate(col.to_numpy(dtype=object)):
        k = uid_int(v)
        if k is not None:
            values[pos] = k
            valid[pos] = True
    return values, valid


def max_uid(df: pd.DataFrame) -> int:
    """The largest valid key in the frame (0 when there is none)."""
    values, valid = _uid_ints(df)
    return int(values[valid].max()) if valid.any() else 0


def assign_sequential_uid(df: pd.DataFrame) -> int:
    """Number a NEW dataset 1..N in its final row order, IN PLACE (int64). Returns N.

    For a dataset that is being born (single-source analysis, ...): any UID the frame carried
    from an earlier export is replaced - BibexPy's own key starts here. Never use it on an
    existing dataset: keys are issued once (see :func:`ensure_uid` for repairs).
    """
    import numpy as np

    df[UID_COL] = np.arange(1, len(df) + 1, dtype="int64")
    return int(len(df))


def ensure_uid(df: pd.DataFrame, floor: Optional[int] = None) -> bool:
    """Give every row a valid, unique sequential UID, IN PLACE. Valid keys are never changed.

    A key is valid when it is a positive integer that no earlier row already holds (the
    first occurrence keeps it). A missing column, a blank, a non-integer value ("r_..."
    text of an old version) or a repeat gets a NEW key greater than ``max(floor, largest
    valid key in df)``, issued in row order. ``floor`` is the analysis's high-water mark
    (``uid_max``): it keeps a repair from re-issuing a key that was handed out earlier
    and may still sit in a snapshot. The column always ends as int64.

    Returns True when anything was added or changed (including a dtype conversion), i.e.
    the caller has to persist the frame - and, when it has an analysis context, raise
    that analysis's uid_max to :func:`max_uid` of the frame BEFORE writing.
    """
    import numpy as np

    n = len(df)
    was_int64 = UID_COL in df.columns and df[UID_COL].dtype == np.dtype("int64")
    values, valid = _uid_ints(df)
    if valid.any():
        # Repeats of an earlier key lose it (positional: the index may repeat after a concat).
        repeat = pd.Series(values).duplicated(keep="first").to_numpy() & valid
        valid = valid & ~repeat
        values[repeat] = 0
    need = ~valid
    count = int(need.sum())
    if count:
        start = max(int(floor or 0), int(values[valid].max()) if valid.any() else 0)
        if start + count >= UID_LIMIT:
            raise ValueError("uid_invariant_violation: key space exhausted")
        values[need] = np.arange(start + 1, start + count + 1, dtype="int64")
    if count == 0 and was_int64:
        return False
    # Positional assignment of a plain array: the frame may carry duplicate index labels.
    df[UID_COL] = values
    return True


def check_uid(df: pd.DataFrame) -> None:
    """Refuse a frame whose UID column has blanks or duplicates (no-op without the column).

    Raises ValueError("uid_invariant_violation: ..."). Called by every dataset write,
    so a frame that would make UID-based operations ambiguous never reaches disk.
    """
    if UID_COL not in df.columns:
        return
    text = _uid_text(df)
    blank_mask = text.str.lower().isin(_UID_BLANKS)
    blank = int(blank_mask.sum())
    dup = int((text.duplicated(keep=False) & ~blank_mask).sum())
    if blank or dup:
        raise ValueError(f"uid_invariant_violation: {blank} blank and {dup} duplicated UID value(s)")


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
    check_uid(df)
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


async def run_uncancellable(fn: Callable[..., Any], *args: Any) -> Any:
    """Run `fn(*args)` in a worker thread and NEVER return early on cancellation.

    A worker thread cannot be cancelled. With a bare ``await asyncio.to_thread(...)`` a
    task cancel ends the task (and so releases the job runner's exclusivity lock) while the
    thread is still writing; a mutating request could then load the old file and publish
    its own result on top of (or under) the job's. Here the call runs as a future that is
    shielded from the cancel; on ``CancelledError`` we keep waiting for the thread to finish
    and only then re-raise, so the task, and with it the lock, ends after the write.
    """
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    cancelled = False
    while not fut.done():
        try:
            await asyncio.shield(fut)
        except asyncio.CancelledError:
            if not fut.done():
                cancelled = True  # cancel arrived mid-write: keep waiting for the thread
                continue
            cancelled = True
        except BaseException:
            break  # the call itself failed: surfaced through fut below
    if fut.cancelled():
        raise asyncio.CancelledError()
    exc = fut.exception()
    if exc is not None:
        raise exc  # a real write error wins over a pending cancel
    if cancelled:
        raise asyncio.CancelledError()
    return fut.result()


async def write_dataset_uncancellable(df: pd.DataFrame, path: Any) -> None:
    """`atomic_write_dataset` for job-final writes; see :func:`run_uncancellable`."""
    await run_uncancellable(atomic_write_dataset, df, path)


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
            # A legacy workbook may predate UIDs: give every row its permanent UID now,
            # so the migrated parquet (and the snapshots taken from it) always carry them.
            ensure_uid(df)
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
