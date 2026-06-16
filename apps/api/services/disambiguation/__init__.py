"""Author and affiliation disambiguation package.

Provides blocking, name similarity, clustering, caching, and an LLM-backed
client to group records referring to the same author or affiliation.
Public entry points are re-exported for use elsewhere in the API."""

from .blocking import build_author_blocks, build_affiliation_blocks
from .similarity import jaro_winkler, normalize_name
from .deepseek_client import DeepSeekClient
from .cache import DisambiguationCache
from .pipeline import run_author_disambiguation, run_affiliation_disambiguation, apply_clusters

__all__ = [
    "build_author_blocks",
    "build_affiliation_blocks",
    "jaro_winkler",
    "normalize_name",
    "DeepSeekClient",
    "DisambiguationCache",
    "run_author_disambiguation",
    "run_affiliation_disambiguation",
    "apply_clusters",
]
