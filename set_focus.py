#!/usr/bin/env python3
"""CEO 动态指令 / 本期焦点 CLI。功能②。
设定后，下一份盘前简报把该主题置顶、加权、扩容；过期自动恢复默认。

用法：
  python set_focus.py "美国金融动向"              # 直接指定焦点主题
  python set_focus.py "最近重点看美国金融动向"     # 也认自然语言口令（钩子用同一入口）
  python set_focus.py --show                        # 查看当前焦点
  python set_focus.py --clear                        # 清除焦点，恢复默认
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cio import focus  # noqa: E402


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("--show", "-s", "show"):
        cur = focus.active_label()
        print("当前焦点：" + (cur or "（无，简报走默认侧重）"))
        return 0
    if args[0] in ("--clear", "-c", "clear"):
        focus.clear_focus()
        print("已清除本期焦点，简报恢复默认。")
        return 0

    text = " ".join(args)
    cmd = focus.parse_command(text)
    if cmd["action"] == "none":
        e = focus.set_focus(text)                 # 当作直接指定主题
        print(f"已设定本期焦点：{e['topic']}（有效期至 {e['until']}）。下一份简报起置顶加权。")
    else:
        print(focus.handle_command(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
