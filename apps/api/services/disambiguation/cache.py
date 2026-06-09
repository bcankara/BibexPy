"""Hash tabanlı LLM yanıt cache'i — aynı blok 2. çağrıda LLM'e gitmesin."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


class DisambiguationCache:
    def __init__(self, project_dir: Path):
        self.path = project_dir / "disambiguation_cache.sqlite"
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL, "
            "created_at REAL NOT NULL"
            ")"
        )
        self.conn.commit()

    @staticmethod
    def hash_key(payload: Any) -> str:
        s = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> Optional[dict]:
        cur = self.conn.execute("SELECT value FROM cache WHERE key = ?", (key,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, value: dict) -> None:
        import time
        self.conn.execute(
            "INSERT OR REPLACE INTO cache(key, value, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
