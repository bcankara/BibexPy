"""Adapter for invoking the core processing modules from the web layer.

Provides helpers to suppress library stdout/stderr, convert pandas
DataFrames into JSON-serializable records, and raise errors as
FastAPI HTTPExceptions.
"""

from __future__ import annotations

import contextlib
import io
import math
from typing import Any

from fastapi import HTTPException


@contextlib.contextmanager
def _suppress_stdio():
    buf_out, buf_err = io.StringIO(), io.StringIO()
    import sys
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf_out, buf_err
    try:
        yield buf_out, buf_err
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def df_to_records(df, max_rows: int | None = None) -> list[dict[str, Any]]:
    """Pandas DataFrame -> JSON-serializable kayıt listesi (NaN -> None)."""
    if df is None:
        return []
    if max_rows is not None:
        df = df.head(max_rows)
    records = df.to_dict(orient="records")
    for rec in records:
        for k, v in rec.items():
            if isinstance(v, float) and math.isnan(v):
                rec[k] = None
    return records


def http_error(message: str, status: int = 500) -> HTTPException:
    return HTTPException(status_code=status, detail=message)
