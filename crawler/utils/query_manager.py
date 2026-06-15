import functools
from pathlib import Path


class QueryManager:
    _queries_dir = Path(__file__).parent.parent / "queries"

    @classmethod
    @functools.lru_cache(maxsize=16)
    def load_query(cls, filename: str) -> str:
        filepath = cls._queries_dir / filename
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()
