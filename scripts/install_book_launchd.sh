#!/bin/bash
# 账本日结定时：**周一至周五 美东 16:30**（收盘后半小时）。
#
# 为什么是收盘后：盯市要用当天的**收盘价**。盘中跑出来的"收盘价"其实是
# 最后一根盘中 K 线，它明天会变——净值曲线上那一天的数字会自己动，
# 而没有任何一处记录说它动过。
#
# 16:30 而不是 16:00：给行情源留出结算时间。取不到价时程序会把那天记成
# 「NAV 不可计算」而不是编一个数，所以晚一点比早一点安全。
#
# 顺带把执行也挂上：run_execute.py 每天跑一次，没到成交日它只会说
# 「等待开盘」，什么都不会做错。
#
# 用法： bash scripts/install_book_launchd.sh
set -e

AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$AGENT_DIR/.venv/bin/python"
HOUR="${CIO_BOOK_HOUR:-16}"
MINUTE="${CIO_BOOK_MINUTE:-30}"

if [ ! -x "$PY" ]; then
  echo "找不到虚拟环境 python：$PY"
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$AGENT_DIR/logs"

install_job () {   # $1=label后缀  $2=脚本  $3=额外参数  $4=分钟偏移
  local LABEL="com.crystal.cio.$1"
  local PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  local M=$(( (MINUTE + $4) % 60 ))
  local H=$(( HOUR + (MINUTE + $4) / 60 ))
  local DAYS=""
  for d in 1 2 3 4 5; do
    DAYS="$DAYS
    <dict>
      <key>Weekday</key><integer>$d</integer>
      <key>Hour</key><integer>$H</integer>
      <key>Minute</key><integer>$M</integer>
    </dict>"
  done
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$AGENT_DIR/$2</string>$3
  </array>
  <key>WorkingDirectory</key><string>$AGENT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CIO_MARKET</key><string>us</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>$DAYS
  </array>
  <key>StandardOutPath</key><string>$AGENT_DIR/logs/$1.out.log</string>
  <key>StandardErrorPath</key><string>$AGENT_DIR/logs/$1.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST
  plutil -lint "$PLIST" >/dev/null || { echo "$LABEL plist 格式有误"; exit 1; }
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  printf '  %-28s 周一至五 %02d:%02d\n' "$LABEL" "$H" "$M"
}

echo "安装账本定时任务（本机时间 = 美东，随夏令时自动跟随）："
install_job "execute" "run_execute.py" "
    <string>--tg</string>" 0
install_job "book"    "run_book.py" "
    <string>--tg</string>
    <string>--pdf</string>" 10

echo ""
echo "  执行先跑，日结晚 10 分钟 —— 当天成交的仓位要先入账，再盯市。"
echo "  日志： tail -f $AGENT_DIR/logs/book.err.log"
echo "  卸载： launchctl unload ~/Library/LaunchAgents/com.crystal.cio.book.plist"
echo "        launchctl unload ~/Library/LaunchAgents/com.crystal.cio.execute.plist"
