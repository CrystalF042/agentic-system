#!/bin/bash
# 安装 launchd 定时：在**本机时区的盘前窗口内**跑盘前情报。
#
# ## 小时数不再写死，从 cio.schedule 算
#
# 2026-09-05 在她机器上查到的现场：plist 排在 **19:30 本机时间（EDT）**，
# 而 09-01 那份错时简报送达是 19:49 —— 19:30 触发、跑了 19 分钟。
#
#   A 股盘前窗口   07:00–09:15 北京
#   北京 07:30  =  EDT 19:30（前一天）
#
# 也就是说：这个 plist 是按**A 股**盘前排的，用机器的美东钟表达。
# 当时没错，`CIO_MARKET` 换成 us 之后没人动它，它就一直错着。
#
# **一个写死的小时数记不住它是为哪个市场写的。** 所以现在默认值从
# `cio.schedule.local_window()` 现算，市场换了它自己就跟着换；
# 并且**拒绝把任务装在盘前窗口之外**（真要装得显式 CIO_PREMARKET_ALLOW_ANY_HOUR=1）。
#
# launchd 跟的是**这台机器的本地时区**，夏令时会自己跟着走。
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
if [ ! -x "$PY" ]; then
  echo "找不到虚拟环境 python：$PY"
  echo "请先在 $AGENT_DIR 建好 .venv 并装依赖（见 README）。"
  exit 1
fi

# **窗口从代码里算，不写死。** 换市场时这里自动跟着换。
#
# `set -e` 下 `WIN=$(失败的命令)` 会**当场退出**，下面那条 `-z` 提示
# 永远打不出来 —— 脚本静默地以 1 退出，什么都不说。所以这里显式兜住，
# 并且**把 stderr 留下来**：吞掉它就等于把"为什么算不出来"也吞掉了。
WIN_ERR=$(mktemp)
set +e
WIN=$("$PY" -c "import sys;sys.path.insert(0,'$AGENT_DIR/src');from cio import schedule as s;lo,hi=s.local_window();print(lo,hi,s.market().get('name',''))" 2>"$WIN_ERR")
WIN_RC=$?
set -e
if [ "$WIN_RC" -ne 0 ] || [ -z "$WIN" ]; then
  echo "算不出盘前窗口（cio.schedule 导入失败，退出码 $WIN_RC）："
  sed 's/^/    /' "$WIN_ERR" | tail -20
  rm -f "$WIN_ERR"
  echo "  先跑： $PY $AGENT_DIR/run_premarket.py --doctor"
  exit 1
fi
rm -f "$WIN_ERR"
WIN_LO="${WIN%% *}"; REST="${WIN#* }"; WIN_HI="${REST%% *}"; MKT="${REST#* }"
WIN_LO_H="${WIN_LO%%:*}"; WIN_HI_H="${WIN_HI%%:*}"

HOUR="${CIO_PREMARKET_HOUR:-$((10#$WIN_LO_H))}"
MINUTE="${CIO_PREMARKET_MINUTE:-0}"

# **拒绝装在窗口之外。** 这一条就是为了不让 19:30 那件事再发生一次：
# 装错了小时数，任务照样"安装成功"，然后每天在错的时间敲一次门——
# 而闸门修好之后，它连简报都不会发，变成每天静悄悄地什么都不做。
if [ "$HOUR" -lt "$((10#$WIN_LO_H))" ] || [ "$HOUR" -ge "$((10#$WIN_HI_H))" ]; then
  echo "拒绝安装：要装在本机 ${HOUR}:$(printf '%02d' "$MINUTE")，"
  echo "  而 ${MKT} 盘前窗口换算到本机是 ${WIN_LO}–${WIN_HI}。"
  echo "  **窗口外的任务不会发出任何简报**（时间闸会让它退出），"
  echo "  它只会每天静悄悄地什么都不做 —— 这正是 2026-09-01 那次的形状。"
  echo "  真要这么装： CIO_PREMARKET_ALLOW_ANY_HOUR=1 bash scripts/install_launchd.sh"
  [ "$CIO_PREMARKET_ALLOW_ANY_HOUR" = "1" ] || exit 1
  echo "  （已显式放行，继续安装）"
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
echo "  周一至周五 本机时间 $(printf '%02d:%02d' "$HOUR" "$MINUTE") 跑盘前情报。"
echo "  ${MKT} 盘前窗口换算到本机：${WIN_LO}–${WIN_HI}（随夏令时自动跟随）。"
echo "  装完请立刻核对： $PY $AGENT_DIR/run_premarket.py --doctor"
echo "  确认下次触发时间： launchctl print gui/\$(id -u)/$LABEL | grep -A3 'next fire'"
echo "  立即试跑一次： launchctl start $LABEL"
echo "  查看日志： tail -f $AGENT_DIR/logs/premarket.out.log"
echo "  卸载： launchctl unload $PLIST"
