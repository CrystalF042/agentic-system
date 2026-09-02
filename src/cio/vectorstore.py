"""LanceDB 向量库封装（本地、文件式、零服务）。嵌入用 nomic（中英双语）。

要点：
- 维度对齐：所有向量（真实/降级）都规整到 embed_dim()，杜绝维度不一致的 ValueError。
- 建表用"数据推断 schema"（LanceDB 从首批数据自动识别向量列），比显式 fixed_size_list 稳。
"""
from __future__ import annotations

from typing import Optional

import lancedb

from .config import LANCE_DIR
from .ollama_client import get_ollama
from .utils import get_logger, now_beijing

log = get_logger("cio.vec")

TABLE = "company_archive"


def _fit(vec: list[float], dim: int) -> list[float]:
    """把向量规整到目标维度（补零或截断），保证入库一致。"""
    vec = list(vec or [])
    if len(vec) == dim:
        return vec
    if len(vec) < dim:
        return vec + [0.0] * (dim - len(vec))
    return vec[:dim]


class VectorStore:
    def __init__(self) -> None:
        self.db = lancedb.connect(str(LANCE_DIR))

    def add_chunks(self, *, sha256: str, title: str, source_url: str, lang: str,
                   region: str, chunks: list[str], published_at: str = "") -> int:
        if not chunks:
            return 0
        oll = get_ollama()
        dim = oll.embed_dim()
        ts = now_beijing().isoformat()
        rows = []
        for i, ch in enumerate(chunks):
            vec = _fit(oll.embed(ch), dim)
            rows.append({
                "vector": vec, "text": ch, "title": title, "source_url": source_url,
                "lang": lang, "region": region, "sha256": sha256, "chunk_id": i,
                "published_at": published_at, "ingested_at": ts,
            })
        try:
            if TABLE in self.db.table_names():
                self.db.open_table(TABLE).add(rows)
            else:
                # 让 LanceDB 从数据推断 schema（向量列自动识别为定长向量）
                self.db.create_table(TABLE, data=rows)
        except Exception as e:
            log.warning("LanceDB 写入失败(%s)", type(e).__name__)
            return 0
        return len(rows)

    def search(self, query: str, k: int = 8) -> list[dict]:
        if TABLE not in self.db.table_names():
            return []
        oll = get_ollama()
        qv = _fit(oll.embed(query), oll.embed_dim())
        try:
            res = self.db.open_table(TABLE).search(qv).limit(k).to_list()
            for r in res:
                r.pop("vector", None)
            return res
        except Exception as e:
            log.warning("检索失败(%s)", type(e).__name__)
            return []

    def count(self) -> int:
        if TABLE not in self.db.table_names():
            return 0
        try:
            return self.db.open_table(TABLE).count_rows()
        except Exception:
            return 0


_vs: Optional[VectorStore] = None


def get_store() -> VectorStore:
    global _vs
    if _vs is None:
        _vs = VectorStore()
    return _vs
