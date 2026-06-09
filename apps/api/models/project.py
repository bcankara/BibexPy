from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = None


class ProjectMeta(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    file_count: int = 0
    raw_size_bytes: int = 0


class UploadedFile(BaseModel):
    name: str
    size: int
    kind: str  # "scopus_csv" | "wos_txt" | "xlsx" | "unknown"
    saved_path: str
