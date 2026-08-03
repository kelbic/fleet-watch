#!/bin/bash
# Deadman для hyperlend executor (адаптация ~/.wc-bot/deadman.sh): если executor.log молчит
# >600с — бот мёртв/завис (cron-watchdog его не поднял) -> TG-алерт, максимум 1/час (штамп),
# штамп снимается при восстановлении. Cron-строку добавляет оператор, не этот скрипт.
LOG=/home/claude-agent/.hyperlend-bot/executor.log
STAMP=/home/claude-agent/.hyperlend-bot/.deadman_alerted
[ -f "$LOG" ] || exit 0
# ВОЗРАСТ ПО СТРОКЕ ГЛАВНОГО ЦИКЛА, А НЕ ПО mtime ФАЙЛА (урок katana, 01.08).
# В executor.log пишет не только рабочий цикл: у katana мемпул-слой держал файл «свежим»
# 11 часов после смерти сканера блоков, и mtime-деадман молчал всё это время (отставание
# 39,506 блоков, процесс жив и жёг ~98% ядра). Живость ЛОГА ≠ живость ЦИКЛА.
# КОНВЕЙЕР, а не <(...): process substitution отдаёт grep'у /dev/fd/N как ИМЯ ФАЙЛА,
# и в части оболочек оно читается пустым — отметка молча не извлекалась, деадман
# сваливался на mtime-фолбэк и снова мерил не цикл, а файл (та самая слепота 01.08).
_stamp() { grep -aoE '^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] hot-set:' | tail -1 | tr -d '[]' | awk '{print $1}'; }
_last=$(tail -n 5000 "$LOG" | _stamp)
[ -n "$_last" ] || _last=$(_stamp < "$LOG")
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
  chat=$(grep '^HL_CHAT_ID=' /home/claude-agent/.hyperlend-bot/env | head -1 | cut -d= -f2- | awk '{print $1}')
  # АДРЕСАТ — АГЕНТ (03.08): мёртвый бот — техническая починка, не решение владельца.
  /home/claude-agent/.fleet-watch/notify.sh hyperlend-deadman 0 \
    "💀 hyperlend executor: главный цикл молчит ${age}s — бот мёртв/завис" 3600 hl-dead > /dev/null
  touch "$STAMP"
else
  rm -f "$STAMP"
fi
