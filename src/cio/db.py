"""SQLite 元数据/审计库（cio.db）。账本与目录：文件是资产本体，这里是索引。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, Optional

from .config import DB_PATH
from .utils import now_beijing

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  title TEXT, source_name TEXT, source_url TEXT,
  lang TEXT, region TEXT, layer TEXT,
  filepath TEXT, sha256 TEXT UNIQUE,
  published_at TEXT, ingested_at TEXT
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, category TEXT,
  url TEXT, last_ok_at TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS watchlist_hits (
  id INTEGER PRIMARY KEY, doc_sha TEXT,
  sector TEXT, target TEXT, signal TEXT, fact TEXT, source_url TEXT, hit_at TEXT
);
CREATE TABLE IF NOT EXISTS briefs (
  id INTEGER PRIMARY KEY, kind TEXT, title TEXT,
  md_path TEXT, pdf_path TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS collection_log (
  id INTEGER PRIMARY KEY, run_at TEXT, kind TEXT,
  fetched INTEGER, deduped INTEGER, hits INTEGER,
  vectors INTEGER, errors TEXT, degraded TEXT
);
CREATE TABLE IF NOT EXISTS run_manifest (
  run_id TEXT PRIMARY KEY, run_at TEXT, kind TEXT, market TEXT,
  universe_src TEXT, universe_snapshot TEXT, universe_hash TEXT,
  price_source TEXT, bench_source TEXT, bench_basis TEXT,
  price_pit INTEGER, universe_pit INTEGER,
  factor_config_version TEXT, params_json TEXT, git_commit TEXT
);
"""


def ensure_columns(con, table: str, cols: dict) -> list:
    """给已存在的表补列。返回真正补上的列名。

    **为什么必须有这个函数，而不是靠 `CREATE TABLE IF NOT EXISTS` 里写新列。**

    `CREATE TABLE IF NOT EXISTS` 对一张**已经存在**的旧表**什么都不做**——
    新加的列不会出现。于是同一段建表脚本里紧跟着的
    `CREATE INDEX ... ON t(新列)` 就会炸：`no such column`。

    这个坑真实发生过（build87 在她机器上）：pc_lineage 的 run_id 迁移写在
    `executescript` **之后**，而 executescript 里的唯一索引正好用到 run_id，
    于是脚本先炸，**那段迁移永远执行不到**——一段专门用来修这个问题的代码，
    被它要修的问题挡在门外。

    所以顺序永远是：**建表 → 补列 → 建索引**。
    """
    have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if not have:                      # 表还不存在 → 由 CREATE TABLE 负责，不在这里造
        return []
    added = []
    for name, decl in cols.items():
        if name not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            added.append(name)
    return added


@contextmanager
def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with connect() as con:
        con.executescript(SCHEMA)


def doc_exists(sha: str) -> bool:
    with connect() as con:
        r = con.execute("SELECT 1 FROM documents WHERE sha256=?", (sha,)).fetchone()
        return r is not None


def insert_document(*, title: str, source_name: str, source_url: str, lang: str,
                    region: str, layer: str, filepath: str, sha256: str,
                    published_at: Optional[str]) -> bool:
    """返回 True=新入库，False=已存在（去重）。"""
    try:
        with connect() as con:
            con.execute(
                """INSERT INTO documents
                   (title, source_name, source_url, lang, region, layer, filepath, sha256, published_at, ingested_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (title, source_name, source_url, lang, region, layer, filepath, sha256,
                 published_at, now_beijing().isoformat()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def upsert_source(name: str, category: str, url: str, status: str) -> None:
    with connect() as con:
        con.execute(
            """INSERT INTO sources (name, category, url, last_ok_at, status)
               VALUES (?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 last_ok_at=excluded.last_ok_at, status=excluded.status""",
            (name, category, url, now_beijing().isoformat() if status == "ok" else None, status),
        )


def insert_watchlist_hit(*, doc_sha: str, sector: str, target: str, signal: str,
                         fact: str, source_url: str) -> None:
    with connect() as con:
        con.execute(
            """INSERT INTO watchlist_hits (doc_sha, sector, target, signal, fact, source_url, hit_at)
               VALUES (?,?,?,?,?,?,?)""",
            (doc_sha, sector, target, signal, fact, source_url, now_beijing().isoformat()),
        )


def insert_brief(kind: str, title: str, md_path: str, pdf_path: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO briefs (kind, title, md_path, pdf_path, created_at) VALUES (?,?,?,?,?)",
            (kind, title, md_path, pdf_path, now_beijing().isoformat()),
        )


def log_collection(*, kind: str, fetched: int, deduped: int, hits: int, vectors: int,
                   errors: Iterable[str], degraded: Iterable[str]) -> None:
    with connect() as con:
        con.execute(
            """INSERT INTO collection_log (run_at, kind, fetched, deduped, hits, vectors, errors, degraded)
               VALUES (?,?,?,?,?,?,?,?)""",
            (now_beijing().isoformat(), kind, fetched, deduped, hits, vectors,
             " | ".join(errors), " | ".join(degraded)),
        )


def insert_manifest(m: dict) -> None:
    """写入一次运行的可复算 manifest（同 run_id 覆盖）。缺字段用空/0 兜底。"""
    cols = ["run_id", "run_at", "kind", "market", "universe_src", "universe_snapshot",
            "universe_hash", "price_source", "bench_source", "bench_basis",
            "price_pit", "universe_pit", "factor_config_version", "params_json", "git_commit"]
    vals = [m.get(c) if c not in ("price_pit", "universe_pit") else int(bool(m.get(c))) for c in cols]
    with connect() as con:
        con.execute(
            f"INSERT OR REPLACE INTO run_manifest ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            vals,
        )


def counts() -> dict:
    with connect() as con:
        d = con.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        h = con.execute("SELECT COUNT(*) c FROM watchlist_hits").fetchone()["c"]
        b = con.execute("SELECT COUNT(*) c FROM briefs").fetchone()["c"]
        return {"documents": d, "watchlist_hits": h, "briefs": b}
