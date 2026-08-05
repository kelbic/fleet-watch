#!/bin/bash
# Deadman для теневой вахты Morpho/HyperEVM (крон */5 мин на Вене).
#
# ВОЗРАСТ ПО СТРОКЕ ГЛАВНОГО ЦИКЛА, а не по mtime (урок katana 01.08: живость лога ≠
# живость цикла). Вахта печатает штамп на ВСЕХ путях, включая путь ошибки, поэтому
# «жив, но ошибается» отличается от «завис» по содержанию строки, а не по её наличию
# (урок midnight 04.08: err-путь без штампа дал ложное 💀).
#
# КОНВЕЙЕР, а не <(...): process substitution отдаёт grep'у /dev/fd/N как имя файла и
# читается пустым — отметка молча не извлекается, деадман сваливается на mtime-фолбэк
# (та самая слепота, что 2 суток жила у трёх ботов из четырёх, 03.08).
#
# Порог 1500с: крон ходит раз в 300с, проход ~8с; три подряд пропуска = отказ.
LOG=/home/claude-agent/morpho-hl-liquidator/data/shadow.log
STAMP=/home/claude-agent/morpho-hl-liquidator/data/.deadman_alerted
[ -f "$LOG" ] || exit 0

_stamp() { grep -aoE '^\[[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\] shadow:' \
           | tail -1 | tr -d '[]' | sed 's/ shadow:$//'; }
_last=$(tail -n 2000 "$LOG" | _stamp)
[ -n "$_last" ] || _last=$(_stamp < "$LOG")
if [ -n "$_last" ]; then
  _now=$(date +%s); _t=$(date -u -d "$_last" +%s 2>/dev/null || echo "")
  age=$(( _now - ${_t:-_now} ))
else
  # строк цикла нет вовсе — это тоже отказ, а не повод молчать
  age=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
fi

if [ "$age" -gt 1500 ]; then
  [ -f "$STAMP" ] && [ $(( $(date +%s) - $(stat -c %Y "$STAMP") )) -lt 3600 ] && exit 0
  # Адресат — АГЕНТ: вставшая read-only вахта чинится без владельца.
  /home/claude-agent/.fleet-watch/notify.sh morpho-hl-deadman 0 \
    "💀 morpho-hl shadow: главный цикл молчит ${age}s (крон */5, порог 1500s)" 3600 mhl-dead \
    > /dev/null
  touch "$STAMP"
else
  rm -f "$STAMP"
fi
