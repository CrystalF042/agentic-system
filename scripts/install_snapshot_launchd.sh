#!/bin/bash
# 安装 launchd 定时：**收盘之后**跑技术快照（Signal Card + 闸门 + 家族分 + 心跳）。
#
# ## 为什么默认不是窗口起点 16:30
#
# 收盘窗口是市场本地 16:30–23:59，但**刚收盘那会儿数据还没落定**：
# 2026-09-04 我们亲眼见过 SPY 最后一根 K 线的收盘价是 NaN（yfinance 尾行），
# 结果全市场 502 只票的大盘超额同时变 null。
#
# 所以默认排在收盘后约两小时（美东 18:00），让尾行落定。
# 想改用 CIO_SNAPSHOT_HOUR；**排在窗口之外会被拒绝**。
#
# ## 小时数从代码算，不写死
#
# 写死的小时数记不住自己是为哪个市场写的——盘前那个 19:30 就是这么来的
# （按 A 股盘前排的，用机器的美东钟表达，换成美股之后没人动它）。
#
# 用法： bash scripts/install_snapshot_launchd.sh
set -e

AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$AGENT_DIR/.venv/bin/python"
LABEL="com.crystal.cio.snapshot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$PY" ]; then
  echo "找不到虚拟环境 python：$PY"
  echo "请先在 $AGENT_DIR 建好 .venv 并装依赖（见 README）。"
  exit 1
fi

# `set -e` 下 `WIN=$(失败的命令)` 会当场退出，下面的提示永远打不出来。
# 所以显式兜住，并且**把 stderr 留下来**：吞掉它就等于把"为什么"也吞掉。
WIN_ERR=$(mktemp)
set +e
WIN=$("$PY" -c "import sys;sys.path.insert(0,'$AGENT_DIR/src');from cio import schedule as s;lo,hi=s.local_window(win=s.SNAPSHOT_WINDOW);print(lo,hi,s.market().get('name',''))" 2>"$WIN_ERR")
WIN_RC=$?
set -e
if [ "$WIN_RC" -ne 0 ] || [ -z "$WIN" ]; then
  echo "算不出收盘窗口（cio.schedule 导入失败，退出码 $WIN_RC）："
  sed 's/^/    /' "$WIN_ERR" | tail -20
  rm -f "$WIN_ERR"
  echo "  先跑： $PY $AGENT_DIR/run_premarket.py --doctor"
  exit 1
fi
rm -f "$WIN_ERR"
WIN_LO="${WIN%% *}"; REST="${WIN#* }"; WIN_HI="${REST%% *}"; MKT="${REST#* }"
WIN_LO_H="${WIN_LO%%:*}"; WIN_HI_H="${WIN_HI%%:*}"

# 默认：窗口起点 + 2 小时（让尾行落定），但不越过窗口终点
DEFAULT_H=$(( 10#$WIN_LO_H + 2 ))
[ "$DEFAULT_H" -ge "$((10#$WIN_HI_H))" ] && DEFAULT_H=$((10#$WIN_LO_H))
HOUR="${CIO_SNAPSHOT_HOUR:-$DEFAULT_H}"
MINUTE="${CIO_SNAPSHOT_MINUTE:-0}"

if [ "$HOUR" -lt "$((10#$WIN_LO_H))" ] || [ "$HOUR" -ge "$((10#$WIN_HI_H))" ]; then
  echo "拒绝安装：要装在本机 ${HOUR}:$(printf '%02d' "$MINUTE")，"
  echo "  而 ${MKT} 收盘窗口换算到本机是 ${WIN_LO}–${WIN_HI}。"
  echo "  **窗口外的任务不会存下任何卡片**（时间闸会让它跳过），"
  echo "  它只会每天留下一份「跳过」的心跳 —— 白占一次唤醒。"
  echo "  真要这么装： CIO_SNAPSHOT_ALLOW_ANY_HOUR=1 bash scripts/install_snapshot_launchd.sh"
  [ "$CIO_SNAPSHOT_ALLOW_ANY_HOUR" = "1" ] || exit 1
  echo "  （已显式放行，继续安装）"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$AGENT_DIR/logs"

# 只在工作日跑：StartCalendarInterval 要给**一个 dict 数组**，一天一个 dict。
# 写成一个 dict 里放五个 Weekday 键是无效的——后面的键覆盖前面的，
# 结果只在周五跑，**而且不会有任何报错**。
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
    <string>$AGENT_DIR/scripts/technical_snapshot.py</string>
  </array>
  <key>WorkingDirectory</key><string>$AGENT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CIO_MARKET</key><string>us</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>$DAYS
  </array>
  <key>StandardOutPath</key><string>$AGENT_DIR/logs/snapshot.out.log</string>
  <key>StandardErrorPath</key><string>$AGENT_DIR/logs/snapshot.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null || { echo "plist 格式有误，未加载"; exit 1; }

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "已安装并加载定时任务：$LABEL"
echo "  周一至周五 本机时间 $(printf '%02d:%02d' "$HOUR" "$MINUTE") 跑收盘技术快照。"
echo "  ${MKT} 收盘窗口换算到本机：${WIN_LO}–${WIN_HI}（随夏令时自动跟随）。"
echo "  默认排在窗口起点后 2 小时 —— **让 yfinance 的尾行落定**，"
echo "  刚收盘那会儿最后一根 K 线可能还是 NaN（2026-09-04 真实发生过）。"
echo "  核对： $PY $AGENT_DIR/run_premarket.py --doctor"
echo "  看心跳： $PY $AGENT_DIR/scripts/heartbeat.py"
echo "  立即试跑： launchctl start $LABEL"
echo "  日志： tail -f $AGENT_DIR/logs/snapshot.out.log"
echo "  卸载： launchctl unload $PLIST"
