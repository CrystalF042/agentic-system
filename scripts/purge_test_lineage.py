#!/usr/bin/env python3
"""把自检写进真实 cio.db 的假 lineage 行清掉。

build76 的自检直接往 `cio.db` 写了 TESTPC 这类假记录。**pc_lineage 是归因分析
的唯一依据**——它记的是"每一次定仓当时的输入"，混进假行之后，半年后的收益拆解
就建立在被污染的样本上，而假行在表里和真行长得一模一样，不会有任何报错。

build77 起自检写临时库（`scripts/test_cro_pc.py` 里把 `db.DB_PATH` 指到 tmp），
所以这个脚本只需要跑一次，把历史遗留清掉。

    python scripts/purge_test_lineage.py            列出要删的行
    python scripts/purge_test_lineage.py --apply    真的删
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cio.config import DB_PATH        # noqa: E402

PREFIXES = ("TESTPC", "TESTVETO", "TEST_")

con = sqlite3.connect(DB_PATH)
try:
    con.execute("SELECT 1 FROM pc_lineage LIMIT 1")
except sqlite3.OperationalError:
    print("pc_lineage 表还不存在——无需清理。")
    raise SystemExit(0)

where = " OR ".join(["ticker LIKE ?"] * len(PREFIXES))
args = [p + "%" for p in PREFIXES]
rows = list(con.execute(
    f"SELECT id, as_of_date, ticker, w_final FROM pc_lineage WHERE {where} ORDER BY id", args))

if not rows:
    print("没有自检遗留的假 lineage 行，台账是干净的。")
    raise SystemExit(0)

print(f"{DB_PATH}\n找到 {len(rows)} 条自检遗留行：")
for rid, d, t, w in rows:
    print(f"  #{rid}  {d}  {t}  w_final={w}")

if "--apply" not in sys.argv:
    print("\n以上是**预览**。确认无误后加 --apply 真的删除。")
    raise SystemExit(0)

con.execute(f"DELETE FROM pc_lineage WHERE {where}", args)
con.commit()
left = con.execute("SELECT COUNT(*) FROM pc_lineage").fetchone()[0]
print(f"\n已删除 {len(rows)} 条。pc_lineage 现有 {left} 条真实记录。")
