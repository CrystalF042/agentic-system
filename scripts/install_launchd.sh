#!/bin/bash
# 安装 launchd 定时：**美东时间每周一至周五早上 07:00** 跑盘前情报。
#
# 为什么是 07:00 ET 而不是 19:00：我们做的是美股。盘前情报要在**美股开盘前**
# 送到（09:30 ET 开盘，07:00 留出两个半小时）。19:00 ET 是收盘三小时之后，
# 那是"昨天的复盘"，不是"今天的盘前"——而它在 Telegram 上长得一模一样。
#
# launchd 跟的是**这台机器的本地时区**。机器在纽约，07:00 就是美东 07:00，
# 夏令时会自己跟着走。机器要是搬去别的时区，这个时间就得重设。
#
# 只在工作日跑：StartCalendarInterval 要给**一个 dict 数组**，一天一个 dict。
# 写成一个 dict 里放五个 Weekday 键是无效的——后面的键覆盖前面的，
# 结果只在周五跑，**而且不会有任何报错**。
#
# 用法： bash scripts/install_launchd.sh      （在 cio-agent 目录下运行）
set -e

AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$AGENT_DIR/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/com.crystal.cio.premarket.plist"
LABEL="com.crystal.cio.premarket"
HOUR="${CIO_PREMARKET_HOUR:-7}"
MINUTE="${CIO_PREMARKET_MINUTE:-0}"

if [ ! -x "$PY" ]; then
  echo "找不到虚拟环境 python：$PY"
  echo "请先在 $AGENT_DIR 建好 .venv 并装依赖（见 README）。"
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$AGENT_DIR/logs"

DAYS=""
for d in 1 2 3 4 5; do
  DAYS="$DAYS
    <dict>
      <key>Weekday</key><integer>$d</integer>
      <key>Hour</key><integer>$HOUR</integer>
      <key>Minute</key><integer>$MINUTE</integer>
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
    <string>$AGENT_DIR/run_premarket.py</string>
  </array>
  <key>WorkingDirectory</key><string>$AGENT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CIO_MARKET</key><string>us</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>$DAYS
  </array>
  <key>StandardOutPath</key><string>$AGENT_DIR/logs/premarket.out.log</string>
  <key>StandardErrorPath</key><string>$AGENT_DIR/logs/premarket.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null || { echo "plist 格式有误，未加载"; exit 1; }

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "已安装并加载定时任务：$LABEL"
echo "  周一至周五 本机时间 $(printf '%02d:%02d' "$HOUR" "$MINUTE") 跑盘前情报（机器在纽约 = 美东，随夏令时自动跟随）。"
echo "  确认下次触发时间： launchctl print gui/\$(id -u)/$LABEL | grep -A3 'next fire'"
echo "  立即试跑一次： launchctl start $LABEL"
echo "  查看日志： tail -f $AGENT_DIR/logs/premarket.out.log"
echo "  卸载： launchctl unload $PLIST"
