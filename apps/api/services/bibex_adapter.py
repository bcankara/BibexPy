"""Çekirdek bibex_core modüllerini web katmanından temiz çağırmak için sarmalayıcı.

- CLI'a özgü print/input çağrılarını yutmaz; sadece stdout/stderr'i bastırır
- DataFrame'leri JSON-friendly hale getirir
- Hataları HTTPException olarak fırlatır

Faz 1'de sadece yapı kuruluyor; gerçek çağrılar Faz 2-6'da eklenecek.
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
