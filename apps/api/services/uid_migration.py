"""One-time conversion of existing analyses to sequential row keys ("seq_v1").

Before this version every row carried a random key ("r_" + 16 hex digits). A row key is now
a plain integer 1..N, assigned once and never reused, like a database primary key. When the
application starts for the first time with this version, every analysis on disk that does
not yet carry ``uid_scheme == "seq_v1"`` in its meta.json is converted here, in the
background, while the UI shows a blocking window.

What is converted, per analysis folder
  * the active dataset (merged.parquet, or a legacy merged.xlsx): rows get 1..N in their
    CURRENT order;
  * every file in ``snapshots/`` (.parquet and legacy .xlsx): a key that is also in the
    dataset gets the same number as there; a key that exists only in snapshots (a row deleted
    after the snapshot was taken) gets N+1, N+2, ... so restoring that snapshot brings the row
    back under its own key;
  * every file that refers to rows by key: borderline_queue.xlsx, match_audit.xlsx,
    Lost_*_Records.xlsx, disambiguation_*.json proposals (schema uid_v1 -> uid_v2) and the
    doi_lookup_*.csv provenance logs.
meta.json gets ``uid_scheme`` and ``uid_max`` LAST. A crash in the middle leaves the analysis
without the marker, so the next start converts it again; the numbering plan is written to
disk first (``uid_migration_plan.json``) so that a re-run reproduces exactly the same numbers
even when part of the files was already rewritten.

The project is held with ``job_runner.mutation`` while it is converted, so no request can
change it half-way; mutating endpoints answer 409 ``dataset_busy_request_running`` meanwhile.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from services import analyses, audit, dataset_io, storage

log = logging.getLogger(__name__)

SCHEME = "seq_v1"
PROPOSALS_SCHEMA_OLD = "uid_v1"
PROPOSALS_SCHEMA_NEW = "uid_v2"
PLAN_NAME = "uid_migration_plan.json"
_UID = dataset_io.UID_COL

# Files in an analysis folder that are never the main dataset (mirrors analyses.active_dataset_path).
_NOT_DATASET = {"statistic.xlsx", "statistic_smart.xlsx", "match_audit.xlsx",
                "conflict_log.xlsx", "borderline_queue.xlsx"}


# ─────────────────────────────────────────────────────────────
#  Status (served by GET /api/system/uid-migration)
# ─────────────────────────────────────────────────────────────

_STATUS_LOCK = threading.Lock()
_STATUS: dict[str, Any] = {}


def _fresh_status() -> dict[str, Any]:
    return {
        "state": "idle",            # idle | running | done | error
        "projects_total": 0,
        "projects_done": 0,
        "analyses_total": 0,
        "analyses_done": 0,
        "current_project_id": None,
        "current_project_name": None,
        "current_analysis": None,
        "error": None,              # short summary when state == "error"
        "errors": [],               # [{project_id, project_name, analysis_id, message}]
        "started_at": None,
        "finished_at": None,
    }


_STATUS.update(_fresh_status())


def reset_status() -> None:
    with _STATUS_LOCK:
        _STATUS.clear()
        _STATUS.update(_fresh_status())


def get_status() -> dict[str, Any]:
    """A copy of the current migration status."""
    with _STATUS_LOCK:
        out = dict(_STATUS)
        out["errors"] = [dict(e) for e in _STATUS["errors"]]
        return out


def _set(**kw: Any) -> None:
    with _STATUS_LOCK:
        _STATUS.update(kw)


def _bump(field: str, by: int = 1) -> None:
    with _STATUS_LOCK:
        _STATUS[field] = int(_STATUS[field]) + by


# ─────────────────────────────────────────────────────────────
#  Key helpers
# ─────────────────────────────────────────────────────────────

def _is_seq_key(text: str) -> bool:
    """True for the text of a valid sequential key: a positive integer."""
    return text.isdigit() and int(text) > 0


def _keys_of(df: pd.DataFrame) -> Optional[list[str]]:
    """Canonical key texts of the UID column (blank -> ""), or None without a UID column."""
    if _UID not in df.columns:
        return None
    return [dataset_io.uid_key(v) for v in df[_UID].tolist()]


def _is_converted(keys: Optional[list[str]]) -> bool:
    """A key column that already holds unique positive integers (and has at least one row)."""
    if not keys:
        return False
    return all(_is_seq_key(k) for k in keys) and len(set(keys)) == len(keys)


def _read_keys_only(path: Path) -> Optional[list[str]]:
    """The UID column of a dataset file as key texts, without reading the other columns.

    None when the file has no UID column (a file that predates UIDs).
    """
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq
        if _UID not in pq.read_schema(path).names:
            return None
        df = pd.read_parquet(path, columns=[_UID])
    else:
        df = pd.read_excel(path, usecols=lambda c: str(c) == _UID)
        if _UID not in df.columns:
            return None
    return [dataset_io.uid_key(v) for v in df[_UID].tolist()]


def _stored_as_int(path: Path) -> bool:
    """True when the UID column of a dataset file is stored with an integer type.

    A workbook cannot tell (a column of whole numbers is read back as int64), so it counts as True.
    """
    if path.suffix.lower() != ".parquet":
        return True
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pq.read_schema(path)
    return _UID in schema.names and pa.types.is_integer(schema.field(_UID).type)


def _main_dataset(adir: Path) -> Optional[Path]:
    """The main dataset file of an analysis folder, WITHOUT triggering the lazy xlsx migration.

    Same preference order as analyses.active_dataset_path: merged.parquet, merged.xlsx,
    then the first dataset-looking file.
    """
    for name in ("merged.parquet", "merged.xlsx"):
        p = adir / name
        if p.is_file():
            return p
    for f in sorted(adir.iterdir()):
        if (f.is_file() and f.suffix.lower() in dataset_io.DATASET_SUFFIXES
                and f.name.lower() not in _NOT_DATASET
                and not f.name.lower().startswith("lost_")):
            return f
    return None


def _snapshot_files(adir: Path) -> list[Path]:
    snaps = adir / "snapshots"
    if not snaps.is_dir():
        return []
    return sorted(f for f in snaps.iterdir()
                  if f.is_file() and f.suffix.lower() in dataset_io.DATASET_SUFFIXES)


# ─────────────────────────────────────────────────────────────
#  Numbering plan
# ─────────────────────────────────────────────────────────────

def _build_plan(adir: Path, dataset: Optional[Path], snapshots: list[Path]) -> dict[str, Any]:
    """Decide the new key of every old key of this analysis. Deterministic.

    {"n": rows of the dataset, "next": first unused number, "map": {old key text: new int},
     "fills": {snapshot file name: [new ints for its blank/repeated key rows, in row order]}}
    """
    mapping: dict[str, int] = {}
    fills: dict[str, list[int]] = {}
    counter = 1
    n = 0

    cur_keys = _read_keys_only(dataset) if dataset is not None else None
    if dataset is not None:
        if cur_keys is None:
            # Predates UIDs: read the row count only (first column is enough).
            df = dataset_io.read_dataset(dataset)
            n = len(df)
            counter = n + 1
        elif _is_converted(cur_keys):
            # Already sequential (e.g. a dataset written by the new code): keep its numbers.
            n = len(cur_keys)
            counter = max(int(k) for k in cur_keys) + 1
        else:
            n = len(cur_keys)
            for i, k in enumerate(cur_keys):
                if k and k not in mapping:
                    mapping[k] = i + 1
            counter = n + 1

    skipped: list[str] = []
    for snap in snapshots:
        try:
            keys = _read_keys_only(snap)
        except Exception as exc:  # noqa: BLE001 - one unreadable snapshot must not block the analysis
            # It could not be restored anyway; it keeps whatever keys it has.
            log.warning("uid migration: snapshot %s is unreadable, left as it is (%s)", snap, exc)
            skipped.append(snap.name)
            continue
        if keys is None:
            continue  # predates UIDs: nothing to map, a later restore assigns fresh keys
        if _is_converted(keys):
            counter = max(counter, max(int(k) for k in keys) + 1)
            continue
        seen: set[str] = set()
        fill: list[int] = []
        for k in keys:
            if not k or k in seen:
                fill.append(counter)
                counter += 1
                continue
            seen.add(k)
            if k not in mapping:
                mapping[k] = counter
                counter += 1
        if fill:
            fills[snap.name] = fill

    return {"version": 1, "n": n, "next": counter, "map": mapping, "fills": fills, "skipped": skipped}


def _atomic_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp~")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _load_or_build_plan(adir: Path, dataset: Optional[Path], snapshots: list[Path]) -> dict[str, Any]:
    plan_path = adir / PLAN_NAME
    if plan_path.is_file():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if (isinstance(plan, dict) and plan.get("version") == 1
                    and isinstance(plan.get("map"), dict) and isinstance(plan.get("fills"), dict)):
                return plan
        except Exception:  # noqa: BLE001 - a corrupt plan is rebuilt below
            log.warning("uid migration: unreadable plan %s, rebuilding", plan_path)
    plan = _build_plan(adir, dataset, snapshots)
    _atomic_text(plan_path, json.dumps(plan))
    return plan


# ─────────────────────────────────────────────────────────────
#  Rewriting dataset files
# ─────────────────────────────────────────────────────────────

def _write_excel_atomic(df: pd.DataFrame, path: Path) -> None:
    """Write a workbook through a temp file and os.replace (readers never see a half-written file).

    The engine is named explicitly: pandas picks it from the file suffix and the temp name
    does not end in ".xlsx".
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp~")
    try:
        with pd.ExcelWriter(tmp, engine="openpyxl") as xw:
            df.to_excel(xw, index=False)
        last: Optional[BaseException] = None
        for _ in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:  # briefly open in a reader (Windows)
                last = exc
                time.sleep(0.25)
        raise last  # type: ignore[misc]
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_keeping_mtime(df: pd.DataFrame, path: Path) -> None:
    """Atomic dataset write that keeps the file's modification time (shown in the snapshot list)."""
    stamp = None
    try:
        st = path.stat()
        stamp = (st.st_atime, st.st_mtime)
    except OSError:
        pass
    if path.suffix.lower() == ".xlsx":
        _write_excel_atomic(df, path)
    else:
        dataset_io.atomic_write_dataset(df, path)
    if stamp is not None:
        try:
            os.utime(path, stamp)
        except OSError:
            pass


def _rewrite_dataset(path: Path) -> tuple[Path, int]:
    """Give the current dataset the keys 1..N in its current order. Returns (final path, rows).

    A legacy merged.xlsx is written as merged.parquet (the existing format migration) and the
    workbook is moved aside afterwards, exactly like dataset_io.ensure_parquet would do.
    """
    keys_before = _read_keys_only(path)
    if _is_converted(keys_before):
        if _stored_as_int(path):
            return path, len(keys_before or [])
        # Sequential numbers stored as floats or text: same numbers, int64 column.
        df = dataset_io.read_dataset(path)
        df[_UID] = pd.Series([int(k) for k in keys_before or []], dtype="int64").to_numpy()
        dataset_io.atomic_write_dataset(df, path)
        return path, len(df)
    df = dataset_io.read_dataset(path)
    df[_UID] = pd.Series(range(1, len(df) + 1), dtype="int64").to_numpy()
    target = path
    if path.suffix.lower() == ".xlsx" and path.name.lower() == "merged.xlsx":
        target = path.with_suffix(".parquet")
    dataset_io.atomic_write_dataset(df, target)
    if target != path:
        # The old workbook is moved into snapshots/ as a rollback copy (best effort). It
        # still holds the old keys, so the snapshot pass that follows converts it too.
        dataset_io.ensure_parquet(path)
    return target, len(df)


def _allocate(plan: dict[str, Any]) -> int:
    """Next unused key of the analysis (the plan's high-water mark moves up)."""
    n = int(plan["next"])
    plan["next"] = n + 1
    plan["dirty"] = True
    return n


def _rewrite_snapshot(path: Path, plan: dict[str, Any]) -> bool:
    """Rewrite one snapshot with the plan's keys. Returns True when the file was changed."""
    df = dataset_io.read_dataset(path)
    keys = _keys_of(df)
    if keys is None:
        return False
    if _is_converted(keys):
        if df[_UID].dtype.kind in "iu":
            return False
        df[_UID] = pd.Series([int(k) for k in keys], dtype="int64").to_numpy()
        _write_keeping_mtime(df, path)
        return True
    mapping: dict[str, int] = plan["map"]
    fill = list(plan["fills"].get(path.name, []))
    seen: set[str] = set()
    new_values: list[int] = []
    for k in keys:
        if not k or k in seen:
            new_values.append(int(fill.pop(0)) if fill else _allocate(plan))
            continue
        seen.add(k)
        new = mapping.get(k)
        if new is None:
            # Not in the plan: a file that appeared after it was made, or the workbook that
            # the format migration moved into snapshots/ (its blank rows). A fresh number.
            new = _allocate(plan)
            mapping[k] = new
        new_values.append(int(new))
    df[_UID] = pd.Series(new_values, dtype="int64").to_numpy()
    _write_keeping_mtime(df, path)
    return True


# ─────────────────────────────────────────────────────────────
#  Translating references
# ─────────────────────────────────────────────────────────────

def _translate(value: Any, mapping: dict[str, int]) -> Any:
    """New key for an old key; anything already sequential, blank or unknown is returned unchanged."""
    text = dataset_io.uid_key(value)
    if not text or _is_seq_key(text):
        return value
    new = mapping.get(text)
    return value if new is None else int(new)


def _translate_borderline_queue(path: Path, mapping: dict[str, int]) -> bool:
    df = pd.read_excel(path)
    changed = False
    for col in ("wos_uid", "scp_uid"):
        if col not in df.columns:
            continue
        old = df[col].tolist()
        new = [_translate(v, mapping) if not pd.isna(v) else "" for v in old]
        if any(dataset_io.uid_key(a) != dataset_io.uid_key(b) for a, b in zip(old, new)):
            changed = True
        df[col] = pd.Series(new, dtype=object).to_numpy()
    if changed:
        _write_excel_atomic(df, path)
    return changed


def _translate_match_audit(path: Path, mapping: dict[str, int]) -> bool:
    df = pd.read_excel(path)
    if "wos_uid" not in df.columns and "scp_uid" not in df.columns:
        return False
    wos = df["wos_uid"].tolist() if "wos_uid" in df.columns else [""] * len(df)
    scp = df["scp_uid"].tolist() if "scp_uid" in df.columns else [""] * len(df)
    uids: list[Any] = []
    for w, s in zip(wos, scp):
        # A matched pair keeps the WoS row's key; the Scopus key is retired.
        new = mapping.get(dataset_io.uid_key(w)) or mapping.get(dataset_io.uid_key(s))
        uids.append(int(new) if new else "")
    pos = list(df.columns).index("wos_uid") if "wos_uid" in df.columns else len(df.columns)
    df = df.drop(columns=[c for c in ("wos_uid", "scp_uid") if c in df.columns])
    df.insert(min(pos, len(df.columns)), "uid", pd.Series(uids, dtype=object).to_numpy())
    _write_excel_atomic(df, path)
    return True


def _translate_lost_records(path: Path, mapping: dict[str, int]) -> bool:
    df = pd.read_excel(path)
    if _UID not in df.columns:
        return False
    old = df[_UID].tolist()
    keys = [dataset_io.uid_key(v) for v in old]
    if all((not k) or _is_seq_key(k) for k in keys):
        return False
    new: list[Any] = []
    for v, k in zip(old, keys):
        if not k or _is_seq_key(k):
            new.append(v if k else "")
        else:
            m = mapping.get(k)
            new.append(int(m) if m is not None else "")
    df[_UID] = pd.Series(new, dtype=object).to_numpy()
    _write_excel_atomic(df, path)
    return True


def _walk_translate_records(node: Any, mapping: dict[str, int]) -> Any:
    """Translate the key lists of a proposal file ("records" and "changed_uids"), recursively.

    A key the plan does not know is dropped (the row is in neither the dataset nor any
    snapshot, so the proposal could never apply to it).
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in ("records", "changed_uids") and isinstance(v, list):
                out[k] = [int(mapping[t]) for t in (dataset_io.uid_key(x) for x in v) if t in mapping]
            else:
                out[k] = _walk_translate_records(v, mapping)
        return out
    if isinstance(node, list):
        return [_walk_translate_records(x, mapping) for x in node]
    return node


def _translate_proposals(path: Path, mapping: dict[str, int]) -> bool:
    props = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(props, dict) or props.get("schema") != PROPOSALS_SCHEMA_OLD:
        return False  # other schemas (positions, already v2) are left exactly as they are
    out = _walk_translate_records(props, mapping)
    out["schema"] = PROPOSALS_SCHEMA_NEW
    _atomic_text(path, json.dumps(out, ensure_ascii=False, indent=2))
    return True


def _translate_provenance_csv(path: Path, mapping: dict[str, int]) -> bool:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    cols = [c for c in ("UID", "duplicate_of") if c in fields]
    if not cols:
        return False
    changed = False
    for r in rows:
        for c in cols:
            v = r.get(c) or ""
            t = dataset_io.uid_key(v)
            if t and not _is_seq_key(t) and t in mapping:
                r[c] = str(mapping[t])
                changed = True
    if not changed:
        return False
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp~")
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return True


def _translate_references(adir: Path, mapping: dict[str, int]) -> int:
    """Rewrite every file of the analysis folder that refers to rows by key. Returns files changed."""
    changed = 0
    bq = adir / "borderline_queue.xlsx"
    if bq.is_file():
        changed += int(_translate_borderline_queue(bq, mapping))
    ma = adir / "match_audit.xlsx"
    if ma.is_file():
        changed += int(_translate_match_audit(ma, mapping))
    for f in sorted(adir.iterdir()):
        if not f.is_file():
            continue
        low = f.name.lower()
        if low.startswith("lost_") and low.endswith("_records.xlsx"):
            changed += int(_translate_lost_records(f, mapping))
        elif low.startswith("disambiguation_") and low.endswith(".json") and "audit" not in low:
            changed += int(_translate_proposals(f, mapping))
        elif low.startswith("doi_lookup_") and low.endswith(".csv"):
            changed += int(_translate_provenance_csv(f, mapping))
    return changed


# ─────────────────────────────────────────────────────────────
#  One analysis / pending scan
# ─────────────────────────────────────────────────────────────

def _drop_dataset_cache() -> None:
    try:
        from services import filter_engine
        filter_engine._DF_CACHE.clear()
    except Exception:  # noqa: BLE001 - the cache is only an optimisation
        pass


def _meta_uid_max(meta: dict[str, Any]) -> int:
    try:
        return max(0, int(meta.get("uid_max")))
    except (TypeError, ValueError):
        return 0


def _raise_uid_floor(adir: Path, value: int) -> None:
    """Raise uid_max in the meta (never lower it) without writing the scheme marker."""
    try:
        with analyses._META_LOCK:
            meta = analyses._read_meta(adir)
            if meta and value > _meta_uid_max(meta):
                meta["uid_max"] = int(value)
                analyses._write_meta(adir, meta)
    except Exception:  # noqa: BLE001 - best effort, the original error is what matters
        log.exception("uid migration: could not record the key floor for %s", adir)


def migrate_analysis(project_id: str, adir: Path) -> dict[str, Any]:
    """Convert one analysis folder. Raises on failure (no marker is written, the next start retries).

    The caller holds the project (``job_runner.mutation``).
    """
    dataset = _main_dataset(adir)
    ds_rows = 0
    snapshots_done = 0
    final: Optional[Path] = None

    # 1) The plan, from the files as they are now (persisted before anything is rewritten).
    plan = _load_or_build_plan(adir, dataset, _snapshot_files(adir))
    mapping: dict[str, int] = plan["map"]

    try:
        # 2) The dataset (a legacy merged.xlsx becomes merged.parquet here).
        if dataset is not None:
            final, ds_rows = _rewrite_dataset(dataset)

        # 3) Snapshots, including a workbook that the step above just moved aside.
        skipped = set(plan.get("skipped", []))
        for snap in _snapshot_files(adir):
            if snap.name in skipped:
                continue
            if _rewrite_snapshot(snap, plan):
                snapshots_done += 1
        if plan.pop("dirty", False):
            _atomic_text(adir / PLAN_NAME, json.dumps(plan))

        # 4) References.
        refs = _translate_references(adir, mapping)
    except Exception:
        # Part of the files may already carry new numbers. The next start continues with the
        # same plan; until then no key issued by normal use may fall into the plan's range.
        try:
            if plan.pop("dirty", False):
                _atomic_text(adir / PLAN_NAME, json.dumps(plan))
        except Exception:  # noqa: BLE001 - best effort
            log.exception("uid migration: could not save the plan of %s", adir)
        _raise_uid_floor(adir, int(plan["next"]) - 1)
        raise

    # 5) The marker, last.
    uid_max = int(plan["next"]) - 1
    # The high-water mark covers every key on disk, snapshots included: a key a snapshot got
    # outside the saved plan (fallback allocation before a hard kill) must never be issued again.
    for path in ([final] if final is not None else []) + _snapshot_files(adir):
        try:
            keys = _read_keys_only(path)
        except Exception:  # noqa: BLE001 - an unreadable snapshot was already skipped above
            continue
        seq = [int(k) for k in (keys or []) if _is_seq_key(k)]
        if seq:
            uid_max = max(uid_max, max(seq))
    with analyses._META_LOCK:
        meta = analyses._read_meta(adir)
        if not meta:
            meta = {"id": adir.name, "created_at": adir.stat().st_mtime, "method": "unknown",
                    "label": adir.name, "file_count": sum(1 for f in adir.iterdir() if f.is_file())}
        meta["uid_scheme"] = SCHEME
        meta["uid_max"] = max(_meta_uid_max(meta), uid_max)   # a high-water mark never goes down
        uid_max = meta["uid_max"]
        analyses._write_meta(adir, meta)
    try:
        (adir / PLAN_NAME).unlink()
    except OSError:
        pass
    _drop_dataset_cache()

    if dataset is not None:
        audit.write(
            project_id,
            kind="format_migration",
            title="Record keys converted to sequential numbers (one-time update)",
            title_key="audit.titles.uidMigration",
            title_params={"analysis": adir.name, "records": ds_rows},
            details={"analysis": adir.name, "records": ds_rows, "snapshots": snapshots_done,
                     "references": refs, "uid_max": uid_max},
            user_action="auto",
            analysis_id=adir.name,
        )
    return {"records": ds_rows, "snapshots": snapshots_done, "references": refs, "uid_max": uid_max}


def _needs_migration(adir: Path) -> bool:
    meta = analyses._read_meta(adir)
    if meta.get("deleted"):
        return False
    if meta.get("source") == "user_run" and not meta.get("completed_at"):
        return False  # a merge that never finished: nothing to convert
    if not meta:
        # A folder without meta.json is only worth converting when it holds a dataset.
        try:
            return _main_dataset(adir) is not None or bool(_snapshot_files(adir))
        except OSError:
            return False
    return meta.get("uid_scheme") != SCHEME


def scan_pending() -> list[dict[str, Any]]:
    """Projects with at least one analysis to convert: [{id, name, analyses: [Path, ...]}]."""
    out: list[dict[str, Any]] = []
    root = storage.settings.storage_path
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / "meta.json").is_file():
            continue
        base = child / "analyses"
        if not base.is_dir():
            continue
        try:
            name = storage._read_meta(child.name).name  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - an unreadable project is not ours to convert
            continue
        pending = [p for p in sorted(base.iterdir())
                   if p.is_dir() and not p.name.startswith("_deleted_") and _needs_migration(p)]
        if pending:
            out.append({"id": child.name, "name": name, "analyses": pending})
    return out


# ─────────────────────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────────────────────

def _acquire(project_id: str, attempts: int = 120, delay: float = 0.5):
    """Enter job_runner.mutation(project_id), waiting while an exclusive job is running."""
    from fastapi import HTTPException
    from jobs.runner import job_runner
    for i in range(attempts):
        cm = job_runner.mutation(project_id)
        try:
            cm.__enter__()
            return cm
        except HTTPException:
            if i == attempts - 1:
                raise
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover


def _run(pending: list[dict[str, Any]], held: dict[str, Any]) -> dict[str, Any]:
    """Convert every pending project. Never raises; errors are collected in the status."""
    try:
        for item in pending:
            pid, pname = item["id"], item["name"]
            cm = held.pop(pid, None)
            _set(current_project_id=pid, current_project_name=pname, current_analysis=None)
            try:
                if cm is None:
                    cm = _acquire(pid)
            except Exception as exc:  # noqa: BLE001 - recorded, the other projects go on
                with _STATUS_LOCK:
                    for adir in item["analyses"]:
                        _STATUS["errors"].append({"project_id": pid, "project_name": pname,
                                                  "analysis_id": adir.name, "message": f"project busy: {exc}"})
                _bump("projects_done")
                continue
            try:
                for adir in item["analyses"]:
                    _set(current_analysis=adir.name)
                    try:
                        migrate_analysis(pid, adir)
                        _bump("analyses_done")
                    except Exception as exc:  # noqa: BLE001 - isolated per analysis
                        log.exception("uid migration failed for %s/%s", pid, adir.name)
                        with _STATUS_LOCK:
                            _STATUS["errors"].append({"project_id": pid, "project_name": pname,
                                                      "analysis_id": adir.name, "message": str(exc)})
            finally:
                try:
                    cm.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
            _bump("projects_done")
    finally:
        for cm in held.values():  # anything left over (an unexpected crash) must not stay locked
            try:
                cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        errors = get_status()["errors"]
        _set(state="error" if errors else "done",
             error=(f"{len(errors)} analysis(es) could not be updated" if errors else None),
             current_analysis=None, current_project_id=None, current_project_name=None,
             finished_at=time.time())
    return get_status()


def _begin(pending: list[dict[str, Any]]) -> None:
    reset_status()
    _set(state="running" if pending else "done",
         projects_total=len(pending),
         analyses_total=sum(len(p["analyses"]) for p in pending),
         started_at=time.time(),
         finished_at=None if pending else time.time())


def run_migration() -> dict[str, Any]:
    """Scan and convert everything synchronously (tests, CLI). Returns the final status."""
    pending = scan_pending()
    _begin(pending)
    if not pending:
        return get_status()
    return _run(pending, {})


def start_background() -> None:
    """Startup hook: scan, hold every pending project right away, convert in a daemon thread.

    The scan only reads meta.json files, so it is quick; the projects are held BEFORE the
    server accepts requests, which closes the window in which a request could re-key a
    project that has not been converted yet. A project that cannot be held now is waited
    for inside the thread.
    """
    pending = scan_pending()
    _begin(pending)
    if not pending:
        return
    held: dict[str, Any] = {}
    for item in pending:
        try:
            held[item["id"]] = _acquire(item["id"], attempts=1)
        except Exception:  # noqa: BLE001 - the thread retries
            pass
    t = threading.Thread(target=_run, args=(pending, held), name="uid-migration", daemon=True)
    t.start()
