#!/bin/bash
# 让 Telegram 控制台常驻（开机自启、崩了自动拉起）。
#
# 控制台是 run_tgbot.py：它挂在那里等你手机上的指令（/pending、批准按钮…）。
# **不常驻也能用**——只是那时按钮点了没人接（转个圈然后什么都不发生），
# 得回电脑上用 run_approve.py。
#
# 用法： bash scripts/install_tgbot_launchd.sh
set -e

AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$AGENT_DIR/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/com.crystal.cio.tgbot.plist"
LABEL="com.crystal.cio.tgbot"

if [ ! -x "$PY" ]; then
  echo "找不到虚拟环境 python：$PY"
  exit 1
fi

if ! grep -q "CIO_CTRL_BOT_TOKEN" "$AGENT_DIR/.env" 2>/dev/null; then
  echo "⚠ .env 里没有 CIO_CTRL_BOT_TOKEN。"
  echo "  控制台会退回用 TELEGRAM_BOT_TOKEN，而那个 token OpenClaw 也在用——"
  echo "  两边抢 getUpdates 会让指令**随机丢失且没有提示**。"
  echo "  去 @BotFather 用 /newbot 另建一个 bot，把 token 写进 .env 再来。"
  echo ""
  read -p "仍然继续安装？(y/N) " yn
  [ "$yn" = "y" ] || exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$AGENT_DIR/logs"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$AGENT_DIR/run_tgbot.py</string>
  </array>
  <key>WorkingDirectory</key><string>$AGENT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CIO_MARKET</key><string>us</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$AGENT_DIR/logs/tgbot.out.log</string>
  <key>StandardErrorPath</key><string>$AGENT_DIR/logs/tgbot.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null || { echo "plist 格式有误，未加载"; exit 1; }

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "控制台已常驻：$LABEL"
echo "  手机上发 /help 试试。"
echo "  日志： tail -f $AGENT_DIR/logs/tgbot.err.log"
echo "  停掉： launchctl unload $PLIST"
