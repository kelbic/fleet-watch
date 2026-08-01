#!/bin/bash
LOG=/home/claude-agent/.wc-bot/executor.log
STAMP=/home/claude-agent/.wc-bot/.deadman_alerted
[ -f "$LOG" ] || exit 0
# ВОЗРАСТ ПО СТРОКЕ ГЛАВНОГО ЦИКЛА, А НЕ ПО mtime ФАЙЛА (урок katana, 01.08).
# В executor.log пишет не только рабочий цикл: у katana мемпул-слой держал файл «свежим»
# 11 часов после смерти сканера блоков, и mtime-деадман молчал всё это время (отставание
# 39,506 блоков, процесс жив и жёг ~98% ядра). Живость ЛОГА ≠ живость ЦИКЛА.
_last=$(grep -aoE '^\\[[0-9]{2}:[0-9]{2}:[0-9]{2}\\] cands=' <(tail -n 5000 "$LOG") | tail -1 | tr -d '[]' | awk '{print $1}')
[ -n "$_last" ] || _last=$(grep -aoE '^\\[[0-9]{2}:[0-9]{2}:[0-9]{2}\\] cands=' "$LOG" | tail -1 | tr -d '[]' | awk '{print $1}')
if [ -n "$_last" ]; then
  _now=$(date +%s); _t=$(date -d "$_last" +%s 2>/dev/null || echo "")
  [ -n "$_t" ] && [ "$_t" -gt "$_now" ] && _t=$(( _t - 86400 ))   # отметка без даты: вчера
  age=$(( _now - ${_t:-_now} ))
else
  age=$(( $(date +%s) - $(stat -c %Y "$LOG") ))   # строк цикла нет вовсе — тоже отказ
fi
if [ "$age" -gt 600 ]; then
  [ -f "$STAMP" ] && [ $(( $(date +%s) - $(stat -c %Y "$STAMP") )) -lt 3600 ] && exit 0
  token=$(grep '^TELEGRAM_BOT_TOKEN=' /home/claude-agent/.claude/channels/telegram/.env 2>/dev/null | cut -d= -f2-)
  chat=$(grep '^export WC_CHAT_ID=' /home/claude-agent/.wc-bot/env | head -1 | cut -d= -f2- | awk '{print $1}')
  [ -n "$token" ] && [ -n "$chat" ] && curl -sm 10 "https://api.telegram.org/bot$token/sendMessage" \
    --data-urlencode "chat_id=$chat" --data-urlencode "text=💀 wc executor: лог молчит ${age}s — бот мёртв/завис" > /dev/null
  touch "$STAMP"
else
  rm -f "$STAMP"
fi
